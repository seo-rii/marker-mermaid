import pytest

from marker_mermaid.candidate_scene import typed_ir_to_scene
from marker_mermaid.config import SecurityProfile
from marker_mermaid.models import (
    MAX_TEXT_CHARS,
    DiagramSceneIR,
    SceneElement,
    SceneRelation,
    VisualEvidence,
)
from marker_mermaid.pipeline import _generated_node_provenance_score
from marker_mermaid.security import MermaidSecurityScanner
from marker_mermaid.serializers import (
    SerializationError,
    scene_to_flowchart,
    serialize_flowchart,
    serialize_swimlane,
)


def _grouped_ir():
    return {
        "direction": "LR",
        "nodes": [
            {"id": "A", "label": "API", "bbox": [0, 0, 10, 10]},
            {"id": "B", "label": "DB", "bbox": [20, 0, 30, 10]},
            {"id": "C", "label": "User", "bbox": [40, 0, 50, 10]},
        ],
        "edges": [{"source": "A", "target": "B"}],
        "groups": [
            {
                "id": "backend",
                "label": "Backend",
                "member_ids": ["A", "B"],
            }
        ],
    }


def test_flowchart_serializes_validated_groups_as_subgraphs():
    code = serialize_flowchart(_grouped_ir())

    assert '    subgraph backend["Backend"]' in code
    assert '        A["API"]' in code
    assert '        B["DB"]' in code
    assert "    end" in code
    assert '    C["User"]' in code
    assert "A --> B" in code
    assert "Groups are retained" not in code


def test_flowchart_without_groups_keeps_the_empty_group_output_identical():
    without_groups = _grouped_ir()
    without_groups.pop("groups")
    empty_groups = {**without_groups, "groups": []}

    assert serialize_flowchart(without_groups) == serialize_flowchart(empty_groups)


@pytest.mark.parametrize(
    ("groups", "message"),
    [
        ([{"id": "g", "member_ids": ["missing"]}], "unknown node"),
        (
            [
                {"id": "g1", "member_ids": ["A"]},
                {"id": "g2", "member_ids": ["A"]},
            ],
            "multiple groups",
        ),
        ([{"id": "A", "member_ids": ["B"]}], "collides with a node"),
        ([{"id": "g", "member_ids": []}], "at least one member"),
        (
            [{"id": "g", "member_ids": ["A"], "children": ["nested"]}],
            "nested flowchart groups",
        ),
    ],
)
def test_flowchart_groups_fail_closed_on_ambiguous_structure(groups, message):
    ir = _grouped_ir()
    ir["groups"] = groups

    with pytest.raises(SerializationError, match=message):
        serialize_flowchart(ir)


def test_flowchart_groups_reject_normalized_and_duplicate_node_id_ambiguity():
    normalized_collision = _grouped_ir()
    normalized_collision["nodes"][0]["id"] = "backend-zone"
    normalized_collision["groups"] = [
        {"id": "backend_zone", "member_ids": ["B"]}
    ]
    with pytest.raises(SerializationError, match="collides with a node"):
        serialize_flowchart(normalized_collision)

    duplicate_node = _grouped_ir()
    duplicate_node["nodes"].append({"id": "A", "label": "Duplicate"})
    with pytest.raises(SerializationError, match="require unique node ids"):
        serialize_flowchart(duplicate_node)


def test_flowchart_rejects_duplicate_node_ids_without_groups() -> None:
    with pytest.raises(SerializationError, match="node ids must be unique"):
        serialize_flowchart(
            {
                "nodes": [
                    {"id": "A", "label": "First"},
                    {"id": "A", "label": "Second"},
                ]
            }
        )


@pytest.mark.parametrize(
    ("edges", "message"),
    [
        ({"source": "A", "target": "B"}, "edges must be a list"),
        (["not-an-object"], "edges must be objects"),
        ([{"source": "A", "target": "missing"}], "unknown endpoint"),
        ([{"source": None, "target": "B"}], "unknown endpoint"),
    ],
)
def test_flowchart_rejects_malformed_or_unresolved_edges(edges, message) -> None:
    with pytest.raises(SerializationError, match=message):
        serialize_flowchart(
            {
                "nodes": [{"id": "A"}, {"id": "B"}],
                "edges": edges,
            }
        )


def test_flowchart_edge_labels_replace_pipe_and_omit_whitespace_only_text() -> None:
    code = serialize_flowchart(
        {
            "nodes": [{"id": "A"}, {"id": "B"}],
            "edges": [
                {"source": "A", "target": "B", "label": "yes|no"},
                {"source": "B", "target": "A", "label": " \t "},
            ],
        }
    )

    assert "A -->|yes∣no| B" in code
    assert "B --> A" in code
    assert "||" not in code


def test_flowchart_text_neutralizes_active_scanner_tokens_and_carriage_returns() -> None:
    hostile = (
        "See https://example.invalid fa:user iconify call(x) // @import %%{init}"
        "\rnext"
    )
    code = serialize_flowchart(
        {
            "acc_title": hostile,
            "acc_description": hostile,
            "nodes": [{"id": "A", "label": hostile}, {"id": "B", "label": "End"}],
            "edges": [{"source": "A", "target": "B", "label": hostile}],
        }
    )

    assert "\r" not in code
    rendered_title = code.replace("\u200b", "").split("accTitle: ", 1)[1].splitlines()[0]
    assert hostile.replace("\r", " ") == rendered_title
    assert '    A -->|"' in code
    report = MermaidSecurityScanner(SecurityProfile.STRICT).scan(code)
    assert report.safe, report.findings


def test_scene_flowchart_reverses_start_only_arrow_endpoints() -> None:
    scene = DiagramSceneIR(
        elements=[
            SceneElement(id="A", role="node", text="Start", bbox=(0, 0, 1, 1)),
            SceneElement(id="B", role="node", text="End", bbox=(2, 0, 3, 1)),
        ],
        relations=[
            SceneRelation(
                id="E",
                source_id="A",
                target_id="B",
                relation_type="edge",
                arrow_at_start=True,
                arrow_at_end=False,
            )
        ],
    )

    code = scene_to_flowchart(scene)

    assert "    B --> A" in code
    assert "    A --> B" not in code


def test_flowchart_candidate_scene_round_trips_group_membership_and_bbox():
    scene = typed_ir_to_scene("flowchart", _grouped_ir())

    assert scene is not None
    assert len(scene.groups) == 1
    group = scene.groups[0]
    assert group.id == "backend"
    assert group.label == "Backend"
    assert group.member_ids == ["A", "B"]
    assert group.bbox == (0.0, 0.0, 30.0, 10.0)


def test_flowchart_group_ids_and_labels_use_the_deterministic_emission_plan():
    ir = _grouped_ir()
    ir["groups"] = [
        {
            "id": "backend-zone",
            "label": 'Backend "Zone"\nPrimary',
            "member_ids": ["A", "B"],
        }
    ]

    code = serialize_flowchart(ir)
    scene = typed_ir_to_scene("flowchart", ir)

    assert 'subgraph backend_zone["Backend &quot;Zone&quot; Primary"]' in code
    assert scene is not None
    assert scene.groups[0].id == "backend_zone"
    assert scene.groups[0].member_ids == ["A", "B"]


def test_flowchart_candidate_scene_uses_emitted_node_member_and_relation_ids():
    ir = {
        "nodes": [
            {"id": "A-B", "label": "First"},
            {"id": "A_B", "label": "Second"},
        ],
        "edges": [{"source": "A-B", "target": "A_B"}],
        "groups": [{"id": "g", "member_ids": ["A-B", "A_B"]}],
    }

    code = serialize_flowchart(ir)
    scene = typed_ir_to_scene("flowchart", ir)

    assert '        A_B["First"]' in code
    assert '        A_B_2["Second"]' in code
    assert "    A_B --> A_B_2" in code
    assert scene is not None
    assert [element.id for element in scene.elements] == ["A_B", "A_B_2"]
    assert scene.groups[0].member_ids == ["A_B", "A_B_2"]
    assert (scene.relations[0].source_id, scene.relations[0].target_id) == (
        "A_B",
        "A_B_2",
    )


def test_normalized_id_collisions_cannot_borrow_another_nodes_provenance():
    source = DiagramSceneIR(
        elements=[
            SceneElement(id="A-B", role="node", text="First", bbox=(0, 0, 1, 1)),
            SceneElement(
                id="A_B",
                role="node",
                text="Second",
                bbox=(2, 0, 3, 1),
                evidence_ids=["ocr-second"],
            ),
        ]
    )
    generated = typed_ir_to_scene(
        "flowchart",
        {
            "nodes": [
                {"id": "A-B", "label": "Changed first"},
                {"id": "A_B", "label": "Changed second"},
            ]
        },
    )
    evidence = [
        VisualEvidence(
            id="ocr-second",
            kind="ocr_token",
            text="Second",
            bbox=(2, 0, 3, 1),
            source_block_ids=["source"],
        )
    ]

    assert generated is not None
    assert _generated_node_provenance_score(generated, source, evidence) == 0


@pytest.mark.parametrize(
    "kind",
    ["ocr_token", "vector_text", "contour", "vlm_observation", "user_edit"],
)
def test_shared_node_evidence_is_revoked_for_every_direct_claimant(kind):
    generated = DiagramSceneIR(
        elements=[
            SceneElement(
                id="A",
                role="node",
                text="First",
                bbox=(0, 0, 1, 1),
                evidence_ids=["shared"],
            ),
            SceneElement(
                id="B",
                role="node",
                text="Second",
                bbox=(2, 0, 3, 1),
                evidence_ids=["shared"],
            ),
        ]
    )

    score = _generated_node_provenance_score(
        generated,
        None,
        [VisualEvidence(id="shared", kind=kind)],
    )

    assert score == 0


def test_shared_inherited_evidence_is_revoked_for_every_generated_node():
    source = DiagramSceneIR(
        elements=[
            SceneElement(
                id="A",
                role="node",
                text="First",
                bbox=(0, 0, 1, 1),
                evidence_ids=["shared"],
            ),
            SceneElement(
                id="B",
                role="node",
                text="Second",
                bbox=(2, 0, 3, 1),
                evidence_ids=["shared"],
            ),
        ]
    )
    generated = DiagramSceneIR(
        elements=[
            SceneElement(id="A", role="node", text="First", bbox=(0, 0, 1, 1)),
            SceneElement(id="B", role="node", text="Second", bbox=(2, 0, 3, 1)),
        ]
    )

    score = _generated_node_provenance_score(
        generated,
        source,
        [VisualEvidence(id="shared", kind="ocr_token", text="First Second")],
    )

    assert score == 0


def test_direct_and_inherited_claims_share_the_same_collision_domain():
    source = DiagramSceneIR(
        elements=[
            SceneElement(id="A", role="node", text="First", bbox=(0, 0, 1, 1)),
            SceneElement(
                id="B",
                role="node",
                text="Second",
                bbox=(2, 0, 3, 1),
                evidence_ids=["shared"],
            ),
        ]
    )
    generated = DiagramSceneIR(
        elements=[
            SceneElement(
                id="A",
                role="node",
                text="First",
                bbox=(0, 0, 1, 1),
                evidence_ids=["shared"],
            ),
            SceneElement(id="B", role="node", text="Second", bbox=(2, 0, 3, 1)),
        ]
    )

    score = _generated_node_provenance_score(
        generated,
        source,
        [VisualEvidence(id="shared", kind="contour", bbox=(0, 0, 3, 1))],
    )

    assert score == 0


def test_unique_alternatives_support_nodes_after_shared_evidence_is_revoked():
    generated = DiagramSceneIR(
        elements=[
            SceneElement(
                id="A",
                role="node",
                text="First",
                bbox=(0, 0, 1, 1),
                evidence_ids=["shared", "unique-a"],
            ),
            SceneElement(
                id="B",
                role="node",
                text="Second",
                bbox=(2, 0, 3, 1),
                evidence_ids=["shared", "unique-b"],
            ),
        ]
    )
    evidence = [
        VisualEvidence(id="shared", kind="ocr_token", text="First Second"),
        VisualEvidence(id="unique-a", kind="ocr_token", text="First"),
        VisualEvidence(id="unique-b", kind="ocr_token", text="Second"),
    ]

    assert _generated_node_provenance_score(generated, None, evidence) == 1


def test_direct_unique_evidence_keeps_inherited_shared_claims_out_of_collision_domain():
    source = DiagramSceneIR(
        elements=[
            SceneElement(
                id="A",
                role="node",
                text="First",
                bbox=(0, 0, 1, 1),
                evidence_ids=["shared"],
            ),
            SceneElement(
                id="B",
                role="node",
                text="Second",
                bbox=(2, 0, 3, 1),
                evidence_ids=["shared"],
            ),
        ]
    )
    generated = DiagramSceneIR(
        elements=[
            SceneElement(
                id="A",
                role="node",
                text="First",
                bbox=(0, 0, 1, 1),
                evidence_ids=["unique-a"],
            ),
            SceneElement(id="B", role="node", text="Second", bbox=(2, 0, 3, 1)),
        ]
    )
    evidence = [
        VisualEvidence(id="shared", kind="ocr_token", text="Second"),
        VisualEvidence(id="unique-a", kind="ocr_token", text="First"),
    ]

    assert _generated_node_provenance_score(generated, source, evidence) == 1


@pytest.mark.parametrize("direct_claim", [True, False])
def test_duplicate_evidence_registry_ids_fail_closed_for_direct_and_inherited_claims(
    direct_claim,
):
    source = DiagramSceneIR(
        elements=[
            SceneElement(
                id="A",
                role="node",
                text="First",
                bbox=(0, 0, 1, 1),
                evidence_ids=["duplicate"],
            )
        ]
    )
    generated = DiagramSceneIR(
        elements=[
            SceneElement(
                id="A",
                role="node",
                text="First",
                bbox=(0, 0, 1, 1),
                evidence_ids=["duplicate"] if direct_claim else [],
            )
        ]
    )
    evidence = [
        VisualEvidence(id="duplicate", kind="ocr_token", text="First"),
        VisualEvidence(id="duplicate", kind="vector_text", text="First"),
    ]

    assert _generated_node_provenance_score(generated, source, evidence) == 0


@pytest.mark.parametrize("kind", ["source_crop", "line_segment", "arrowhead"])
def test_context_and_connector_evidence_cannot_attribute_generated_nodes(kind):
    generated = DiagramSceneIR(
        elements=[
            SceneElement(
                id="A",
                role="node",
                text="First",
                bbox=(0, 0, 1, 1),
                evidence_ids=["context"],
            )
        ]
    )

    score = _generated_node_provenance_score(
        generated,
        None,
        [VisualEvidence(id="context", kind=kind, bbox=(0, 0, 1, 1))],
    )

    assert score == 0


def test_node_and_relation_reuse_is_not_a_node_evidence_collision():
    generated = DiagramSceneIR(
        elements=[
            SceneElement(
                id="A",
                role="node",
                text="First",
                bbox=(0, 0, 1, 1),
                evidence_ids=["node-a"],
            ),
            SceneElement(
                id="B",
                role="node",
                text="Second",
                bbox=(2, 0, 3, 1),
                evidence_ids=["node-b"],
            ),
        ],
        relations=[
            SceneRelation(
                id="edge",
                source_id="A",
                target_id="B",
                relation_type="sequence",
                evidence_ids=["node-b"],
            )
        ],
    )
    evidence = [
        VisualEvidence(id="node-a", kind="contour", bbox=(0, 0, 1, 1)),
        VisualEvidence(id="node-b", kind="contour", bbox=(2, 0, 3, 1)),
    ]

    assert _generated_node_provenance_score(generated, None, evidence) == 1


def test_swimlane_candidate_scene_records_emitted_lane_groups():
    scene = typed_ir_to_scene(
        "swimlane",
        {
            "lanes": [
                {"id": "user", "label": "User", "nodes": [{"id": "A", "label": "Ask"}]},
                {
                    "id": "system",
                    "label": "System",
                    "nodes": [{"id": "B", "label": "Answer"}],
                },
            ],
            "edges": [{"source": "A", "target": "B"}],
        },
    )

    assert scene is not None
    assert [(group.id, group.member_ids) for group in scene.groups] == [
        ("user", ["A"]),
        ("system", ["B"]),
    ]


def test_swimlane_reuses_the_group_plan_for_colliding_and_missing_node_ids():
    ir = {
        "lanes": [
            {"id": "left", "nodes": [{"id": "A-B", "label": "First"}]},
            {
                "id": "right",
                "nodes": [{"id": "A_B", "label": "Second"}, {"label": "Fallback"}],
            },
        ],
        "edges": [
            {"source": "A-B", "target": "A_B", "bidirectional": True},
        ],
        "groups": [{"id": "ignored", "member_ids": ["missing"]}],
    }

    from_swimlane = serialize_swimlane(ir)
    scene = typed_ir_to_scene("swimlane", ir)

    assert 'subgraph left["left"]' in from_swimlane
    assert 'subgraph right["right"]' in from_swimlane
    assert '        A_B["First"]' in from_swimlane
    assert '        A_B_2["Second"]' in from_swimlane
    assert '        N3["Fallback"]' in from_swimlane
    assert "subgraph ignored" not in from_swimlane
    assert "A_B <--> A_B_2" in from_swimlane
    assert scene is not None
    assert [element.id for element in scene.elements] == ["A_B", "A_B_2", "N3"]
    assert [group.member_ids for group in scene.groups] == [["A_B"], ["A_B_2", "N3"]]


@pytest.mark.parametrize(
    "lanes",
    [
        [{"id": "empty", "nodes": []}],
        [
            {"id": "lane-x", "nodes": [{"id": "A"}]},
            {"id": "lane_x", "nodes": [{"id": "B"}]},
        ],
        [{"id": "A", "nodes": [{"id": "A"}]}],
    ],
)
def test_swimlane_fails_closed_on_empty_or_colliding_lanes(lanes):
    with pytest.raises(SerializationError):
        serialize_swimlane({"lanes": lanes, "edges": []})


def test_swimlane_rejects_nested_intent_before_flattening():
    with pytest.raises(SerializationError, match="nested swimlane lanes"):
        serialize_swimlane(
            {
                "lanes": [
                    {
                        "id": "outer",
                        "nodes": [{"id": "A"}],
                        "children": [{"id": "inner"}],
                    }
                ]
            }
        )


def test_flowchart_group_count_cap_fails_before_scene_model_construction():
    ir = {
        "nodes": [{"id": f"N{index}"} for index in range(1001)],
        "groups": [
            {"id": f"G{index}", "member_ids": [f"N{index}"]}
            for index in range(1001)
        ],
        "edges": [],
    }

    with pytest.raises(SerializationError, match="group count exceeds"):
        serialize_flowchart(ir)
    assert typed_ir_to_scene("flowchart", ir) is None


@pytest.mark.parametrize("label", [["not", "text"], "x" * (MAX_TEXT_CHARS + 1)])
def test_group_labels_are_bounded_scalar_strings(label):
    ir = _grouped_ir()
    ir["groups"][0]["label"] = label

    with pytest.raises(SerializationError, match="group label"):
        serialize_flowchart(ir)
    assert typed_ir_to_scene("flowchart", ir) is None

    swimlane_ir = {
        "lanes": [{"id": "lane", "label": label, "nodes": [{"id": "A"}]}]
    }
    with pytest.raises(SerializationError, match="group label"):
        serialize_swimlane(swimlane_ir)
    assert typed_ir_to_scene("swimlane", swimlane_ir) is None
