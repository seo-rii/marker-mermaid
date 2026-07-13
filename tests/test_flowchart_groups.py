import pytest

from marker_mermaid.candidate_scene import typed_ir_to_scene
from marker_mermaid.models import (
    MAX_TEXT_CHARS,
    DiagramSceneIR,
    SceneElement,
    VisualEvidence,
)
from marker_mermaid.pipeline import _generated_node_provenance_score
from marker_mermaid.serializers import (
    SerializationError,
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
