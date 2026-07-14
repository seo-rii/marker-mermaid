from collections import UserDict

import pytest

from marker_mermaid.candidate_scene import typed_ir_semantic_texts, typed_ir_to_scene
from marker_mermaid.models import (
    MAX_EVIDENCE_REFS,
    MAX_SCENE_ELEMENTS,
    MAX_SCENE_GROUPS,
    MAX_SCENE_RELATIONS,
)
from marker_mermaid.scoring import ocr_recall
from marker_mermaid.serializers import (
    SerializationError,
    serialize_architecture,
    serialize_architecture_flowchart_fallback,
)
from marker_mermaid.serializers_phase2 import serialize_phase2


def test_flowchart_typed_ir_preserves_explicit_direction_and_arrows():
    scene = typed_ir_to_scene(
        "flowchart",
        {
            "direction": "LR",
            "nodes": [
                {"id": "A", "label": "Start", "bbox": [0, 0, 10, 10]},
                {"id": "B", "label": "End", "bbox": [20, 0, 30, 10]},
            ],
            "edges": [{"source": "A", "target": "B"}],
        },
    )

    assert scene is not None
    assert scene.reading_direction == "LR"
    assert scene.elements[0].bbox == (0, 0, 10, 10)
    assert scene.relations[0].arrow_at_end


def test_generated_scene_arrows_follow_serializer_visible_direction() -> None:
    flowchart = typed_ir_to_scene(
        "flowchart",
        {
            "nodes": [{"id": "A"}, {"id": "B"}],
            "edges": [
                {
                    "source": "A",
                    "target": "B",
                    "arrow_at_start": True,
                    "arrow_at_end": False,
                }
            ],
        },
    )
    bidirectional = typed_ir_to_scene(
        "flowchart",
        {
            "nodes": [{"id": "A"}, {"id": "B"}],
            "edges": [
                {
                    "source": "A",
                    "target": "B",
                    "bidirectional": True,
                    "arrow_at_start": False,
                    "arrow_at_end": False,
                }
            ],
        },
    )
    sequence = typed_ir_to_scene(
        "sequence",
        {
            "participants": ["A", "B"],
            "messages": [
                {
                    "source": "A",
                    "target": "B",
                    "arrow_at_start": True,
                    "arrow_at_end": False,
                }
            ],
        },
    )

    assert flowchart is not None and bidirectional is not None and sequence is not None
    assert (
        flowchart.relations[0].arrow_at_start,
        flowchart.relations[0].arrow_at_end,
    ) == (False, True)
    assert (
        bidirectional.relations[0].arrow_at_start,
        bidirectional.relations[0].arrow_at_end,
    ) == (True, True)
    assert (
        sequence.relations[0].arrow_at_start,
        sequence.relations[0].arrow_at_end,
    ) == (False, True)


def test_sequence_scene_uses_collision_free_emitted_participant_ids() -> None:
    scene = typed_ir_to_scene(
        "sequence",
        {
            "participants": [
                {"id": "A-B", "label": "First"},
                {"id": "A B", "label": "Second"},
            ],
            "messages": [{"source": "A-B", "target": "A B", "label": "Next"}],
        },
    )

    assert scene is not None
    assert [(element.id, element.text) for element in scene.elements] == [
        ("A_B", "First"),
        ("A_B_2", "Second"),
    ]
    assert (scene.relations[0].source_id, scene.relations[0].target_id) == (
        "A_B",
        "A_B_2",
    )


def test_sequence_scene_assigns_collision_free_emitted_message_ids() -> None:
    scene = typed_ir_to_scene(
        "sequence",
        {
            "participants": ["A", "B"],
            "messages": [
                {"source": "A", "target": "B"},
                {"id": "generated-relation-1", "source": "B", "target": "A"},
                {"id": "generated-relation-1", "source": "A", "target": "B"},
            ],
        },
    )

    assert scene is not None
    assert [relation.id for relation in scene.relations] == [
        "generated-relation-1",
        "generated-relation-2",
        "generated-relation-3",
    ]


def test_mindmap_scene_uses_serializer_ids_even_when_logical_ids_repeat() -> None:
    scene = typed_ir_to_scene(
        "mindmap",
        {
            "root": {
                "id": "duplicate",
                "label": "Root",
                "children": [
                    {"id": "duplicate", "label": "First"},
                    {"id": "duplicate", "label": "Second"},
                ],
            }
        },
    )

    assert scene is not None
    assert [(element.id, element.text) for element in scene.elements] == [
        ("root", "Root"),
        ("node_2", "First"),
        ("node_3", "Second"),
    ]
    assert [(relation.source_id, relation.target_id) for relation in scene.relations] == [
        ("root", "node_2"),
        ("root", "node_3"),
    ]


def test_swimlane_and_mindmap_flatten_only_resolved_relations():
    swimlane = typed_ir_to_scene(
        "swimlane",
        {
            "lanes": [
                {"nodes": [{"id": "A", "label": "A"}]},
                {"nodes": [{"id": "B", "label": "B"}]},
            ],
            "edges": [
                {"source": "A", "target": "B"},
                {"source": "A", "target": "missing"},
            ],
        },
    )
    mindmap = typed_ir_to_scene(
        "mindmap",
        {"root": {"label": "Root", "children": [{"id": "child", "label": "Child"}]}},
    )

    assert swimlane is not None and len(swimlane.relations) == 1
    assert mindmap is not None
    assert [item.id for item in mindmap.elements] == ["root", "node_2"]
    assert (
        mindmap.relations[0].source_id,
        mindmap.relations[0].target_id,
    ) == ("root", "node_2")


def test_partial_phase_one_scenes_use_the_serializers_unreadable_label() -> None:
    cases = [
        ("flowchart", {"nodes": [{"id": "A"}]}),
        ("generic_network", {"nodes": [{"id": "A"}]}),
        ("swimlane", {"lanes": [{"id": "lane", "nodes": [{"id": "A"}]}]}),
        ("bpmn", {"lanes": [{"id": "lane", "nodes": [{"id": "A"}]}]}),
        ("mindmap", {"root": {"id": "A"}}),
    ]

    for diagram_type, ir in cases:
        scene = typed_ir_to_scene(diagram_type, ir)

        assert scene is not None
        assert [element.text for element in scene.elements] == ["[unreadable]"]


def test_unsupported_or_empty_typed_ir_is_unavailable():
    assert typed_ir_to_scene("gantt", {"sections": []}) is None
    assert typed_ir_to_scene("flowchart", {"nodes": []}) is None


@pytest.mark.parametrize(
    ("diagram_type", "ir"),
    [
        ("pie", {"slices": [{"label": "A", "value": 1}]}),
        (
            "xychart",
            {
                "x_axis": {"categories": ["A"]},
                "y_axis": {"min": 0, "max": 1},
                "series": [{"kind": "line", "values": [1]}],
            },
        ),
        (
            "quadrant",
            {
                "x_axis": {"low": "Low", "high": "High"},
                "y_axis": {"low": "Low", "high": "High"},
                "points": [{"label": "A", "x": 0.5, "y": 0.5}],
            },
        ),
    ],
)
def test_phase_three_core_charts_have_no_structural_scene_adapter(
    diagram_type: str, ir: dict[str, object]
) -> None:
    assert typed_ir_to_scene(diagram_type, ir) is None


def test_radar_has_no_structural_scene_adapter() -> None:
    assert (
        typed_ir_to_scene(
            "radar",
            {
                "dimensions": [{"id": "speed", "label": "Speed"}],
                "series": [{"id": "car", "label": "Car", "values": [1]}],
            },
        )
        is None
    )


def test_sankey_scene_preserves_node_flow_geometry_and_provenance() -> None:
    scene = typed_ir_to_scene(
        "sankey",
        {
            "nodes": [
                {
                    "id": "source",
                    "label": "Source",
                    "bbox": [1, 2, 11, 12],
                    "evidence_ids": ["ocr-source", "contour-source"],
                },
                {
                    "id": "sink",
                    "label": "Sink",
                    "bbox": [21, 2, 31, 12],
                    "evidence_ids": ["ocr-sink"],
                },
            ],
            "flows": [
                {
                    "id": "flow-1",
                    "source": "source",
                    "target": "sink",
                    "value": 3,
                    "evidence_ids": ["line-flow-1"],
                }
            ],
        },
    )

    assert scene is not None
    assert [(item.id, item.text, item.bbox, item.evidence_ids) for item in scene.elements] == [
        ("source", "Source", (1, 2, 11, 12), ["ocr-source", "contour-source"]),
        ("sink", "Sink", (21, 2, 31, 12), ["ocr-sink"]),
    ]
    assert [
        (
            relation.id,
            relation.source_id,
            relation.target_id,
            relation.evidence_ids,
        )
        for relation in scene.relations
    ] == [("flow-1", "source", "sink", ["line-flow-1"])]


def test_gantt_scene_preserves_task_and_section_labels_without_schedule_metadata():
    scene = typed_ir_to_scene(
        "gantt",
        {
            "sections": [
                {
                    "id": "review",
                    "title": "Review phase",
                    "tasks": [
                        {
                            "id": "t1",
                            "label": "Review payment",
                            "status": "done",
                            "start": "2026-07-01",
                            "end": "2026-07-02",
                            "evidence_ids": ["ocr-task"],
                        }
                    ],
                }
            ]
        },
    )

    assert scene is not None
    assert [(item.id, item.text) for item in scene.elements] == [("t1", "Review payment")]
    assert scene.elements[0].evidence_ids == ["ocr-task"]
    assert [(group.id, group.label, group.member_ids) for group in scene.groups] == [
        ("review", "Review phase", ["t1"])
    ]


def test_class_semantic_texts_include_members_parameters_and_cardinalities():
    ir = {
        "classes": [
            {
                "id": "service",
                "label": "Payment Service",
                "members": [
                    {"name": "status", "type": "String"},
                    {
                        "name": "authorize",
                        "kind": "method",
                        "parameters": ["amount"],
                        "return_type": "bool",
                    },
                ],
            },
            {"id": "gateway", "label": "Gateway"},
        ],
        "relations": [
            {
                "source": "service",
                "target": "gateway",
                "label": "authorizes",
                "source_cardinality": "one",
                "target_cardinality": "many",
            }
        ],
    }
    scene = typed_ir_to_scene("class", ir)

    assert scene is not None
    texts = typed_ir_semantic_texts("class", ir, scene)
    assert (
        ocr_recall(
            ["Payment Service Gateway authorizes String status authorize amount bool one many"],
            "",
            generated_texts=texts,
        )
        == 1
    )


def test_er_semantic_texts_include_rendered_attribute_fields():
    ir = {
        "entities": [
            {
                "id": "customer",
                "label": "Customer Account",
                "attributes": [
                    {
                        "type": "uuid",
                        "name": "customer_id",
                        "keys": ["PK"],
                        "comment": "stable identifier",
                    }
                ],
            },
            {"id": "order", "label": "Order"},
        ],
        "relationships": [{"source": "customer", "target": "order", "label": "places"}],
    }
    scene = typed_ir_to_scene("er", ir)

    assert scene is not None
    texts = typed_ir_semantic_texts("er", ir, scene)
    assert (
        ocr_recall(
            ["Customer Account Order places uuid customer_id PK stable identifier"],
            "",
            generated_texts=texts,
        )
        == 1
    )


def test_serializer_aware_texts_exclude_hidden_generic_text_and_task_ids():
    class_ir = {"classes": [{"id": "A", "text": "Hidden class text"}], "relations": []}
    er_ir = {"entities": [{"id": "B", "text": "Hidden entity text"}], "relationships": []}
    gantt_ir = {
        "sections": [
            {
                "tasks": [
                    {
                        "id": "internal-task-id",
                        "text": "Secret payload",
                        "start": "2026-01-01",
                        "end": "2026-01-02",
                    }
                ]
            }
        ]
    }
    class_scene = typed_ir_to_scene("class", class_ir)
    er_scene = typed_ir_to_scene("er", er_ir)
    gantt_scene = typed_ir_to_scene("gantt", gantt_ir)

    assert class_scene is not None and er_scene is not None and gantt_scene is not None
    class_texts = list(typed_ir_semantic_texts("class", class_ir, class_scene))
    er_texts = list(typed_ir_semantic_texts("er", er_ir, er_scene))
    gantt_texts = list(typed_ir_semantic_texts("gantt", gantt_ir, gantt_scene))
    assert class_texts == ["A"]
    assert er_texts == ["B"]
    assert gantt_texts == ["Tasks", "Task 1"]
    assert ocr_recall(["Hidden class text"], "", generated_texts=class_texts) == 0
    assert ocr_recall(["Hidden entity text"], "", generated_texts=er_texts) == 0
    assert ocr_recall(["Secret payload internal-id"], "", generated_texts=gantt_texts) == 0


def test_phase_one_semantic_texts_exclude_unrendered_aliases_and_relation_labels():
    architecture_ir = {
        "services": [
            {
                "id": "api",
                "name": "Concealed service alias",
                "text": "Secret service payload",
            },
            {"id": "db", "label": "Database"},
        ],
        "edges": [
            {
                "source": "api",
                "target": "db",
                "label": "Invisible connector caption",
            }
        ],
    }
    sequence_ir = {
        "participants": [
            {"id": "client", "text": "Concealed caller payload"},
            {"id": "api", "label": "Payment API"},
        ],
        "messages": [{"source": "client", "target": "api", "label": "Request"}],
    }
    architecture_scene = typed_ir_to_scene("architecture", architecture_ir)
    sequence_scene = typed_ir_to_scene("sequence", sequence_ir)

    assert architecture_scene is not None and sequence_scene is not None
    architecture_texts = list(
        typed_ir_semantic_texts("architecture", architecture_ir, architecture_scene)
    )
    sequence_texts = list(typed_ir_semantic_texts("sequence", sequence_ir, sequence_scene))
    assert architecture_texts == ["Concealed service alias", "Database"]
    assert sequence_texts == ["client", "Payment API", "Request"]
    assert (
        ocr_recall(
            ["Secret payload Invisible connector caption"],
            "",
            generated_texts=architecture_texts,
        )
        == 0
    )
    assert ocr_recall(["Concealed caller payload"], "", generated_texts=sequence_texts) == 0


def test_architecture_group_scene_matches_native_and_flowchart_visible_label() -> None:
    ir = {
        "groups": [{"id": "core services", "role": "hidden-group-role"}],
        "services": [
            {
                "id": "api",
                "label": "API",
                "group": "core services",
                "bbox": [10, 20, 30, 40],
            },
            {
                "id": "db",
                "label": "Database",
                "group": "core services",
                "bbox": [50, 60, 80, 90],
            },
        ],
        "edges": [{"source": "api", "target": "db"}],
    }

    scene = typed_ir_to_scene("architecture", ir)
    native = serialize_architecture(ir)
    fallback = serialize_architecture_flowchart_fallback(ir)

    assert scene is not None
    assert [(group.id, group.label, group.member_ids, group.bbox) for group in scene.groups] == [
        ("core_services", "core_services", ["api", "db"], (10, 20, 80, 90))
    ]
    assert scene.groups[0].role == "group"
    assert 'group core_services(cloud)["core_services"]' in native
    assert 'subgraph core_services["core_services"]' in fallback
    texts = list(typed_ir_semantic_texts("architecture", ir, scene))
    assert texts == ["API", "Database", "core_services"]
    assert ocr_recall(["core_services"], "", generated_texts=texts) == 1
    assert ocr_recall(["core services"], "", generated_texts=texts) == 0


def test_architecture_scene_does_not_invent_missing_groups_or_members() -> None:
    no_groups = typed_ir_to_scene(
        "architecture",
        {
            "services": [
                {"id": "api", "label": "API"},
            ]
        },
    )
    unknown_group = typed_ir_to_scene(
        "architecture",
        {"services": [{"id": "api", "label": "API", "group": "undeclared"}]},
    )
    empty_group = typed_ir_to_scene(
        "architecture",
        {
            "groups": [{"id": "external zone", "bbox": [1, 2, 3, 4]}],
            "services": [{"id": "api", "label": "API"}],
        },
    )

    assert no_groups is not None and no_groups.groups == []
    assert unknown_group is None
    assert empty_group is not None
    assert [
        (group.id, group.label, group.member_ids, group.bbox) for group in empty_group.groups
    ] == [("external_zone", "external_zone", [], (1, 2, 3, 4))]


def test_architecture_edge_plan_is_bounded_for_serializer_and_scene() -> None:
    ir = {
        "services": [{"id": "api"}, {"id": "db"}],
        "edges": [{"source": "api", "target": "db"} for _index in range(MAX_SCENE_RELATIONS + 1)],
    }

    with pytest.raises(SerializationError, match="edge count exceeds"):
        serialize_architecture(ir)
    assert typed_ir_to_scene("architecture", ir) is None


def test_architecture_scene_uses_collision_free_serializer_service_ids() -> None:
    ir = {
        "services": [
            {
                "id": "A-B",
                "name": "Primary",
                "text": "Concealed alpha payload",
                "role": "hidden-service-role",
                "shape": "diamond",
            },
            {"id": "A B", "name": "Secondary", "text": "Concealed beta payload"},
            {"name": "Generated", "text": "Concealed gamma payload"},
        ],
        "edges": [{"source": "A-B", "target": "A B"}],
    }

    scene = typed_ir_to_scene("architecture", ir)
    native = serialize_architecture(ir)
    fallback = serialize_architecture_flowchart_fallback(ir)

    assert scene is not None
    assert [(element.id, element.text) for element in scene.elements] == [
        ("A_B", "Primary"),
        ("A_B_2", "Secondary"),
        ("S3", "Generated"),
    ]
    assert all(element.role == "node" and element.shape is None for element in scene.elements)
    assert (scene.relations[0].source_id, scene.relations[0].target_id) == (
        "A_B",
        "A_B_2",
    )
    assert 'service A_B(server)["Primary"]' in native
    assert 'service A_B_2(server)["Secondary"]' in native
    assert "A_B:R --> L:A_B_2" in native
    assert 'A_B["Primary"]' in fallback
    assert 'A_B_2["Secondary"]' in fallback
    texts = list(typed_ir_semantic_texts("architecture", ir, scene))
    assert ocr_recall(["Concealed alpha beta gamma payload"], "", generated_texts=texts) == 0


def test_architecture_fallback_relation_texts_match_visible_mermaid_only() -> None:
    cases = [
        (
            "deployment",
            {
                "groups": [{"id": "runtime zone"}],
                "nodes": [
                    {
                        "id": "app-node",
                        "name": "App",
                        "group": "runtime zone",
                        "role": "hidden-node-role",
                        "shape": "diamond",
                    },
                    {"id": "app node", "name": "DB", "group": "runtime zone"},
                ],
                "artifacts": [{"name": "Binary"}],
                "links": [
                    {
                        "source": "app-node",
                        "target": "app node",
                        "label": "Hidden JDBC",
                        "arrow_at_start": True,
                        "arrow_at_end": False,
                        "style": "dashed",
                        "relation_type": "hidden_type",
                        "semantic_relation": "causal",
                    }
                ],
            },
            "Hidden JDBC",
            [("app_node", "App"), ("app_node_2", "DB"), ("S3", "Binary")],
            ("app_node", "app_node_2"),
            ["app_node", "app_node_2"],
        ),
        (
            "component",
            {
                "groups": [{"id": "component zone"}],
                "components": [
                    {"id": "web", "label": "Web", "group": "component zone"},
                    {"id": "auth", "label": "Auth", "group": "component zone"},
                ],
                "interfaces": [{"name": "Port"}],
                "dependencies": [{"source": "web", "target": "auth", "label": "Hidden OAuth"}],
            },
            "Hidden OAuth",
            [("web", "Web"), ("auth", "Auth"), ("S3", "Port")],
            ("web", "auth"),
            ["web", "auth"],
        ),
    ]

    for (
        diagram_type,
        ir,
        hidden_label,
        expected_elements,
        expected_endpoints,
        expected_group_members,
    ) in cases:
        scene = typed_ir_to_scene(diagram_type, ir)

        assert scene is not None
        assert [(element.id, element.text) for element in scene.elements] == expected_elements
        assert all(element.role == "node" and element.shape is None for element in scene.elements)
        assert len(scene.relations) == 1 and scene.relations[0].label is None
        assert (scene.relations[0].source_id, scene.relations[0].target_id) == (expected_endpoints)
        assert (
            scene.relations[0].arrow_at_start,
            scene.relations[0].arrow_at_end,
        ) == (False, True)
        assert scene.relations[0].line_style is None
        assert scene.relations[0].relation_type == "generated_connector"
        assert scene.relations[0].semantic_relation == "unknown"
        assert len(scene.groups) == 1
        assert scene.groups[0].label.endswith("_zone")
        assert scene.groups[0].member_ids == expected_group_members
        texts = list(typed_ir_semantic_texts(diagram_type, ir, scene))
        assert ocr_recall([hidden_label], "", generated_texts=texts) == 0


@pytest.mark.parametrize(
    ("diagram_type", "ir", "visible_texts", "hidden_texts"),
    [
        (
            "deployment",
            {
                "nodes": [
                    {
                        "id": "app",
                        "label": "Application",
                        "group": "runtime",
                        "bbox": [10, 20, 30, 40],
                        "evidence_ids": ["ocr-app"],
                        "stereotype": "executionEnvironment",
                    }
                ],
                "artifacts": [
                    {
                        "id": "image",
                        "name": "Image",
                        "group": "runtime",
                        "bbox": [50, 60, 70, 80],
                        "evidence_ids": ["ocr-image"],
                        "containment": "app",
                    }
                ],
                "groups": [
                    {
                        "id": "runtime",
                        "label": "Runtime",
                        "bbox": [1, 2, 90, 100],
                        "evidence_ids": ["contour-runtime"],
                    }
                ],
                "links": [
                    {
                        "id": "raw-link",
                        "source": "app",
                        "target": "image",
                        "label": "Hidden JDBC",
                        "bidirectional": True,
                        "bbox": [30, 40, 50, 60],
                        "evidence_ids": ["arrow-jdbc"],
                    }
                ],
                "edges": [
                    {
                        "source": "image",
                        "target": "app",
                        "label": "Hidden legacy edge",
                        "evidence_ids": ["legacy-arrow"],
                    }
                ],
            },
            ["Application", "Image", "Runtime"],
            ["Hidden JDBC legacy edge executionEnvironment containment"],
        ),
        (
            "component",
            {
                "components": [
                    {
                        "id": "web",
                        "label": "Web",
                        "group": "application",
                        "bbox": [10, 20, 30, 40],
                        "evidence_ids": ["ocr-web"],
                        "stereotype": "component",
                    }
                ],
                "interfaces": [
                    {
                        "id": "auth",
                        "name": "Auth port",
                        "group": "application",
                        "bbox": [50, 60, 70, 80],
                        "evidence_ids": ["ocr-auth"],
                        "provided": True,
                    }
                ],
                "groups": [
                    {
                        "id": "application",
                        "label": "Application",
                        "bbox": [1, 2, 90, 100],
                        "evidence_ids": ["contour-application"],
                    }
                ],
                "dependencies": [
                    {
                        "id": "raw-dependency",
                        "source": "web",
                        "target": "auth",
                        "label": "Hidden OAuth",
                        "bbox": [30, 40, 50, 60],
                        "evidence_ids": ["arrow-oauth"],
                    }
                ],
                "edges": [
                    {
                        "source": "auth",
                        "target": "web",
                        "label": "Hidden legacy edge",
                        "evidence_ids": ["legacy-arrow"],
                    }
                ],
            },
            ["Web", "Auth port", "Application"],
            ["Hidden OAuth legacy edge component provided"],
        ),
    ],
)
def test_architecture_fallback_scene_keeps_provenance_but_not_lost_metadata(
    diagram_type: str,
    ir: dict[str, object],
    visible_texts: list[str],
    hidden_texts: list[str],
) -> None:
    scene = typed_ir_to_scene(diagram_type, ir)

    assert scene is not None
    first_evidence = "ocr-app" if diagram_type == "deployment" else "ocr-web"
    second_evidence = "ocr-image" if diagram_type == "deployment" else "ocr-auth"
    assert [(element.text, element.bbox, element.evidence_ids) for element in scene.elements] == [
        (visible_texts[0], (10, 20, 30, 40), [first_evidence]),
        (visible_texts[1], (50, 60, 70, 80), [second_evidence]),
    ]
    assert [(group.label, group.member_ids, group.bbox) for group in scene.groups] == [
        (visible_texts[2], [scene.elements[0].id, scene.elements[1].id], (1, 2, 90, 100))
    ]
    assert len(scene.relations) == 1
    assert scene.relations[0].id == "generated-relation-1"
    assert scene.relations[0].label is None
    assert scene.relations[0].evidence_ids == [
        "arrow-jdbc" if diagram_type == "deployment" else "arrow-oauth"
    ]
    texts = list(typed_ir_semantic_texts(diagram_type, ir, scene))
    assert texts == visible_texts
    assert ocr_recall(hidden_texts, "", generated_texts=texts) == 0


def test_architecture_fallback_scene_does_not_revive_legacy_edges() -> None:
    deployment = typed_ir_to_scene(
        "deployment",
        {
            "nodes": [{"id": "app"}, {"id": "db"}],
            "artifacts": [],
            "links": [],
            "edges": [{"source": "app", "target": "db"}],
        },
    )
    component = typed_ir_to_scene(
        "component",
        {
            "components": [{"id": "web"}, {"id": "auth"}],
            "interfaces": [],
            "dependencies": [],
            "edges": [{"source": "web", "target": "auth"}],
        },
    )

    assert deployment is not None and deployment.relations == []
    assert component is not None and component.relations == []


def test_software_fallback_scenes_ignore_non_emitted_duplicate_relation_ids() -> None:
    cases = [
        (
            "architecture",
            {
                "services": [{"id": "a"}, {"id": "b"}],
                "edges": [
                    {"id": "same", "source": "a", "target": "b"},
                    {"id": "same", "source": "b", "target": "a"},
                ],
            },
        ),
        (
            "deployment",
            {
                "nodes": [{"id": "a"}, {"id": "b"}],
                "artifacts": [],
                "links": [
                    {"id": "same", "source": "a", "target": "b"},
                    {"id": "same", "source": "b", "target": "a"},
                ],
            },
        ),
        (
            "component",
            {
                "components": [{"id": "a"}, {"id": "b"}],
                "interfaces": [],
                "dependencies": [
                    {"id": "same", "source": "a", "target": "b"},
                    {"id": "same", "source": "b", "target": "a"},
                ],
            },
        ),
        (
            "usecase",
            {
                "actors": [{"id": "a"}],
                "use_cases": [{"id": "b"}],
                "relations": [
                    {"id": "same", "source": "a", "target": "b"},
                    {"id": "same", "source": "b", "target": "a"},
                ],
            },
        ),
    ]

    for diagram_type, ir in cases:
        scene = typed_ir_to_scene(diagram_type, ir)

        assert scene is not None
        assert [relation.id for relation in scene.relations] == [
            "generated-relation-1",
            "generated-relation-2",
        ]


def test_usecase_scene_uses_serializer_relation_label_precedence() -> None:
    ir = {
        "actors": [
            {
                "id": "shopper",
                "label": "Shopper",
                "role": "hidden-actor-role",
                "shape": "diamond",
            }
        ],
        "use_cases": [
            {"id": "checkout", "label": "Checkout"},
            {"id": "refund", "label": "Refund"},
        ],
        "relations": [
            {
                "id": "raw-association",
                "source": "shopper",
                "target": "checkout",
                "type": "CUSTOM_INCLUDE",
                "label": "Hidden relation alias",
                "bidirectional": True,
                "arrow_at_end": False,
                "style": "dashed",
                "evidence_ids": ["arrow-checkout"],
            },
            {
                "id": "raw-request",
                "source": "shopper",
                "target": "refund",
                "label": "requests",
                "evidence_ids": ["arrow-refund"],
            },
        ],
    }

    scene = typed_ir_to_scene("usecase", ir)
    code = serialize_phase2("usecase", ir)[0]

    assert scene is not None
    assert scene.reading_direction == "LR"
    assert scene.elements[0].role == "node" and scene.elements[0].shape == "stadium"
    assert [element.shape for element in scene.elements[1:]] == ["round", "round"]
    assert 'shopper(["Shopper"])' in code
    assert 'checkout("Checkout")' in code
    assert 'refund("Refund")' in code
    assert "shopper -->|CUSTOM_INCLUDE| checkout" in code
    assert "shopper -->|requests| refund" in code
    assert "<-->" not in code and "-.->" not in code
    assert "raw-association" not in code and "raw-request" not in code
    assert "Hidden relation alias" not in code
    assert [relation.id for relation in scene.relations] == [
        "generated-relation-1",
        "generated-relation-2",
    ]
    assert [relation.label for relation in scene.relations] == ["CUSTOM_INCLUDE", "requests"]
    assert [relation.evidence_ids for relation in scene.relations] == [
        ["arrow-checkout"],
        ["arrow-refund"],
    ]
    assert [(relation.arrow_at_start, relation.arrow_at_end) for relation in scene.relations] == [
        (False, True),
        (False, True),
    ]
    assert all(relation.line_style is None for relation in scene.relations)
    assert all(
        relation.relation_type == "generated_connector" and relation.semantic_relation == "unknown"
        for relation in scene.relations
    )
    texts = list(typed_ir_semantic_texts("usecase", ir, scene))
    assert ocr_recall(["CUSTOM_INCLUDE requests"], "", generated_texts=texts) == 1
    assert ocr_recall(["Hidden relation alias"], "", generated_texts=texts) == 0


def test_usecase_scene_and_code_suppress_unsupported_system_boundaries() -> None:
    ir = {
        "actors": [
            {
                "id": "shopper",
                "label": "Shopper",
                "bbox": [10, 20, 30, 40],
                "evidence_ids": ["ocr-shopper"],
                "stereotype": "primary actor",
            }
        ],
        "use_cases": [
            {
                "id": "checkout",
                "label": "Checkout",
                "bbox": [50, 60, 70, 80],
                "evidence_ids": ["ocr-checkout"],
                "system_boundary": "Hidden per-case boundary",
            }
        ],
        "relations": [
            {
                "source": "shopper",
                "target": "checkout",
                "type": "association",
                "evidence_ids": ["arrow-association"],
            }
        ],
        "groups": [
            {
                "id": "hidden-system",
                "label": "Hidden system boundary",
                "member_ids": ["checkout"],
            }
        ],
        "system_boundary": "Hidden checkout system",
        "system_boundaries": [{"id": "hidden", "label": "Hidden boundary record"}],
    }

    scene = typed_ir_to_scene("usecase", ir)
    code = serialize_phase2("usecase", ir)[0]

    assert scene is not None
    assert scene.groups == []
    assert [(element.bbox, element.evidence_ids) for element in scene.elements] == [
        ((10, 20, 30, 40), ["ocr-shopper"]),
        ((50, 60, 70, 80), ["ocr-checkout"]),
    ]
    assert scene.relations[0].evidence_ids == ["arrow-association"]
    assert "subgraph" not in code
    texts = list(typed_ir_semantic_texts("usecase", ir, scene))
    assert texts == ["Shopper", "Checkout", "association"]
    assert (
        ocr_recall(
            ["Hidden enclosure zone record per-case primary stereotype"],
            "",
            generated_texts=texts,
        )
        == 0
    )


@pytest.mark.parametrize(
    "relation",
    [
        "not-an-object",
        {"source": "actor", "target": "missing"},
        {"source": "missing", "target": "case"},
    ],
)
def test_usecase_scene_rejects_malformed_or_dangling_relations(relation: object) -> None:
    ir = {
        "actors": [{"id": "actor"}],
        "use_cases": [{"id": "case"}],
        "relations": [relation],
    }

    with pytest.raises(SerializationError):
        serialize_phase2("usecase", ir)
    assert typed_ir_to_scene("usecase", ir) is None


@pytest.mark.parametrize("direction", [None, "sideways"])
def test_usecase_scene_matches_flowchart_invalid_direction_fallback(direction) -> None:
    scene = typed_ir_to_scene(
        "usecase",
        {
            "direction": direction,
            "actors": [{"id": "actor"}],
            "use_cases": [{"id": "case"}],
            "relations": [],
        },
    )

    assert scene is not None and scene.reading_direction == "TB"


def test_usecase_relation_plan_is_bounded_for_serializer_and_scene() -> None:
    ir = {
        "actors": [{"id": "actor"}],
        "use_cases": [{"id": "case"}],
        "relations": [
            {"source": "actor", "target": "case"} for _index in range(MAX_SCENE_RELATIONS + 1)
        ],
    }

    with pytest.raises(SerializationError, match="relation count exceeds"):
        serialize_phase2("usecase", ir)
    assert typed_ir_to_scene("usecase", ir) is None


def test_usecase_node_plan_is_bounded_for_serializer_and_scene() -> None:
    ir = {
        "actors": [{"id": f"actor-{index}"} for index in range(MAX_SCENE_ELEMENTS)],
        "use_cases": [{"id": "case"}],
        "relations": [],
    }

    with pytest.raises(SerializationError, match="node count exceeds"):
        serialize_phase2("usecase", ir)
    assert typed_ir_to_scene("usecase", ir) is None


def test_usecase_scene_reuses_cross_family_ids_names_and_defaults() -> None:
    ir = {
        "actors": [
            {"id": "shared-id", "name": "Shopper", "text": "Hidden actor text"},
            {"name": "Operator"},
        ],
        "use_cases": [
            {"id": "shared id", "name": "Checkout", "text": "Hidden case text"},
            {"name": "Refund"},
        ],
        "relations": [{"source": "shared-id", "target": "shared id", "type": "association"}],
    }

    scene = typed_ir_to_scene("usecase", ir)
    code = serialize_phase2("usecase", ir)[0]

    assert scene is not None
    assert [(element.id, element.text) for element in scene.elements] == [
        ("shared_id", "Shopper"),
        ("Actor2", "Operator"),
        ("usecase_shared_id", "Checkout"),
        ("usecase_UseCase2", "Refund"),
    ]
    assert (scene.relations[0].source_id, scene.relations[0].target_id) == (
        "shared_id",
        "usecase_shared_id",
    )
    assert 'shared_id(["Shopper"])' in code
    assert 'Actor2(["Operator"])' in code
    assert 'usecase_shared_id("Checkout")' in code
    assert 'usecase_UseCase2("Refund")' in code
    assert "shared_id -->|association| usecase_shared_id" in code
    texts = list(typed_ir_semantic_texts("usecase", ir, scene))
    assert (
        ocr_recall(["Shopper Operator Checkout Refund association"], "", generated_texts=texts) == 1
    )
    assert ocr_recall(["Hidden actor case text"], "", generated_texts=texts) == 0


def test_usecase_scene_avoids_second_order_actor_namespace_collisions() -> None:
    ir = {
        "actors": [{"id": "a-b"}, {"id": "usecase_y"}],
        "use_cases": [{"id": "a b"}, {"id": "y"}],
        "relations": [{"source": "usecase_y", "target": "y", "type": "association"}],
    }

    scene = typed_ir_to_scene("usecase", ir)

    assert scene is not None
    assert [element.id for element in scene.elements] == [
        "a_b",
        "usecase_y",
        "usecase_a_b",
        "usecase_y_2",
    ]
    assert (scene.relations[0].source_id, scene.relations[0].target_id) == (
        "usecase_y",
        "usecase_y_2",
    )


def test_c4_semantic_texts_follow_architecture_fallback_visible_labels_only():
    ir = {
        "title": "Hidden diagram title",
        "elements": [
            {"id": "user", "label": "User", "kind": "person"},
            {
                "id": "api",
                "name": "Payment API",
                "text": "Hidden element text",
                "technology": "Hidden runtime",
                "boundary": "payments",
            },
            {"id": "worker", "label": "Shared", "boundary": "core-boundary"},
            {"id": "queue", "label": "Shared", "boundary": "core-boundary"},
        ],
        "boundaries": [
            {"id": "payments", "label": "Payments"},
            {"id": "core-boundary"},
        ],
        "relations": [
            {
                "source": "user",
                "target": "api",
                "label": "Hidden relation label",
                "technology": "Hidden protocol",
            }
        ],
    }
    scene = typed_ir_to_scene("c4", ir)

    assert scene is not None
    texts = list(typed_ir_semantic_texts("c4", ir, scene))
    assert texts == ["Payments", "core_boundary", "User", "Payment API", "Shared", "Shared"]
    assert ocr_recall(["Shared Shared"], "", generated_texts=texts) == 1
    assert (
        ocr_recall(
            [
                "Hidden diagram title element text runtime relation label protocol",
            ],
            "",
            generated_texts=texts,
        )
        == 0
    )


def test_c4_scene_matches_fallback_identity_topology_and_visible_evidence() -> None:
    ir = {
        "level": "container",
        "boundaries": [
            {
                "id": "결제 영역",
                "name": "Hidden boundary name",
                "description": "Hidden boundary description",
                "role": "hidden-boundary-role",
                "bbox": [1, 2, 99, 100],
            }
        ],
        "elements": [
            {
                "id": "A-B",
                "label": "API",
                "name": "Hidden API name",
                "kind": "container",
                "boundary": "결제 영역",
                "text": "Hidden API text",
                "technology": "Hidden Python runtime",
                "description": "Hidden API description",
                "role": "hidden-node-role",
                "shape": "diamond",
                "bbox": [10, 20, 30, 40],
                "evidence_ids": ["ocr-api", "contour-api"],
            },
            {
                "id": "A B",
                "name": "Database",
                "kind": "container_database",
                "boundary": "결제 영역",
                "bbox": [50, 60, 70, 80],
            },
            {"id": "same", "label": "First duplicate", "boundary": "결제 영역"},
            {"id": "same", "label": "Second duplicate", "boundary": "결제 영역"},
            {"kind": "person", "boundary": "결제 영역"},
        ],
        "relations": [
            {
                "id": "raw-duplicate-id",
                "source": "A-B",
                "target": "A B",
                "label": "Hidden relation label",
                "technology": "Hidden HTTPS protocol",
                "bidirectional": True,
                "style": "dashed",
                "semantic_relation": "dependency",
                "relation_type": "hidden-relation-type",
                "evidence_ids": ["arrow-ab"],
            },
            {
                "id": "raw-duplicate-id",
                "source": "same",
                "target": "A-B",
            },
        ],
    }

    scene = typed_ir_to_scene("c4", ir)

    assert scene is not None
    assert [(element.id, element.text) for element in scene.elements] == [
        ("A_B", "API"),
        ("A_B_2", "Database"),
        ("same", "First duplicate"),
        ("same_2", "Second duplicate"),
        ("S5", "S5"),
    ]
    assert all(element.role == "node" and element.shape is None for element in scene.elements)
    assert scene.elements[0].bbox == (10, 20, 30, 40)
    assert scene.elements[0].evidence_ids == ["ocr-api", "contour-api"]
    assert [(group.id, group.label, group.member_ids, group.bbox) for group in scene.groups] == [
        (
            "group_1",
            "G1",
            ["A_B", "A_B_2", "same", "same_2", "S5"],
            (1, 2, 99, 100),
        )
    ]
    assert scene.groups[0].role == "group"
    assert [relation.id for relation in scene.relations] == [
        "generated-relation-1",
        "generated-relation-2",
    ]
    assert [(relation.source_id, relation.target_id) for relation in scene.relations] == [
        ("A_B", "A_B_2"),
        ("same", "A_B"),
    ]
    assert [(relation.arrow_at_start, relation.arrow_at_end) for relation in scene.relations] == [
        (True, True),
        (False, True),
    ]
    assert scene.relations[0].evidence_ids == ["arrow-ab"]
    assert all(
        relation.label is None
        and relation.line_style is None
        and relation.semantic_relation == "unknown"
        and relation.relation_type == "generated_connector"
        for relation in scene.relations
    )
    texts = list(typed_ir_semantic_texts("c4", ir, scene))
    assert texts == ["G1", "API", "Database", "First duplicate", "Second duplicate", "S5"]
    assert ocr_recall(["G1 API Database First duplicate Second S5"], "", generated_texts=texts) == 1
    assert (
        ocr_recall(
            [
                "Hidden boundary name description text Python runtime HTTPS "
                "protocol relation label",
            ],
            "",
            generated_texts=texts,
        )
        == 0
    )


@pytest.mark.parametrize(
    "ir",
    [
        {"level": "landscape", "elements": [{"id": "api"}]},
        {"elements": [{"id": "api", "kind": "unsupported"}]},
        {
            "elements": [{"id": "api", "boundary": "missing"}],
            "boundaries": [{"id": "known"}],
        },
        {
            "elements": [{"id": "A-B", "boundary": "A B"}],
            "boundaries": [{"id": "A B"}],
        },
        {
            "elements": [
                {"id": "one", "boundary": "core-zone"},
                {"id": "two", "boundary": "core zone"},
            ],
            "boundaries": [{"id": "core-zone"}, {"id": "core zone"}],
        },
    ],
)
def test_c4_scene_and_serializer_reject_the_same_invalid_structure(
    ir: dict[str, object],
) -> None:
    with pytest.raises(SerializationError):
        serialize_phase2("c4", ir)
    assert typed_ir_to_scene("c4", ir) is None


@pytest.mark.parametrize("resource", ["elements", "relations", "boundaries"])
def test_c4_scene_and_serializer_share_resource_caps(resource: str) -> None:
    ir: dict[str, object] = {
        "elements": [{"id": "api"}, {"id": "db"}],
        "boundaries": [],
        "relations": [],
    }
    if resource == "elements":
        ir["elements"] = [{"id": f"service-{index}"} for index in range(MAX_SCENE_ELEMENTS + 1)]
    elif resource == "relations":
        ir["relations"] = [
            {"source": "api", "target": "db"} for _index in range(MAX_SCENE_RELATIONS + 1)
        ]
    else:
        ir["boundaries"] = [{"id": f"boundary-{index}"} for index in range(MAX_SCENE_GROUPS + 1)]

    with pytest.raises(SerializationError, match="exceeds.*limit|count exceeds"):
        serialize_phase2("c4", ir)
    assert typed_ir_to_scene("c4", ir) is None


def test_c4_empty_boundary_is_native_only_and_preserved_in_scene() -> None:
    ir = {
        "elements": [{"label": "Ungrouped", "bbox": [10, 20, 30, 40]}],
        "boundaries": [{"bbox": [1, 2, 3, 4]}],
        "relations": [],
    }

    code, emitted_type, _reason = serialize_phase2("c4", ir)
    scene = typed_ir_to_scene("c4", ir)

    assert emitted_type == "architecture"
    assert 'group G1(cloud)["G1"]' in code
    assert 'service S1(server)["Ungrouped"]' in code
    assert scene is not None
    assert [(element.id, element.text) for element in scene.elements] == [("S1", "Ungrouped")]
    assert [(group.id, group.label, group.member_ids, group.bbox) for group in scene.groups] == [
        ("G1", "G1", [], (1, 2, 3, 4))
    ]
    with pytest.raises(SerializationError, match="has no services"):
        serialize_phase2("c4", ir, native_runtime_valid=False)


@pytest.mark.parametrize(
    ("record_type", "evidence_ids"),
    [
        ("element", 1),
        ("element", "ocr-api"),
        ("element", [f"ocr-{index}" for index in range(MAX_EVIDENCE_REFS + 1)]),
        ("relation", 1),
        ("relation", "arrow-api-db"),
        ("relation", [f"arrow-{index}" for index in range(MAX_EVIDENCE_REFS + 1)]),
    ],
)
def test_c4_invalid_provenance_does_not_break_publication_or_scene(
    record_type: str,
    evidence_ids: object,
) -> None:
    ir = {
        "elements": [{"id": "api", "label": "API"}, {"id": "db", "label": "DB"}],
        "relations": [{"source": "api", "target": "db"}],
    }
    record = ir["elements"][0] if record_type == "element" else ir["relations"][0]
    record["evidence_ids"] = evidence_ids

    code, emitted_type, _reason = serialize_phase2("c4", ir)
    scene = typed_ir_to_scene("c4", ir)

    assert emitted_type == "architecture"
    assert 'service api(server)["API"]' in code
    assert scene is not None
    if record_type == "element":
        assert scene.elements[0].evidence_ids == []
    else:
        assert scene.relations[0].evidence_ids == []


def test_requirement_semantic_texts_mirror_normalized_native_fields_and_defaults():
    ir = {
        "title": "Secret heading",
        "requirements": [
            {
                "id": "REQ-1",
                "requirement_id": "R-001",
                "text": "User can pay",
                "label": "Concealed primary caption",
                "type": "functional",
                "risk": "high",
                "verify_method": "test",
            },
            {"id": "REQ 1", "label": "Backup path"},
        ],
        "elements": [
            {
                "id": "REQ_1",
                "type": "system",
                "label": "Concealed component caption",
                "docref": "api.md",
            }
        ],
        "relations": [
            {
                "source": "REQ_1",
                "target": "REQ-1",
                "type": "satisfies",
                "label": "Concealed connector caption",
            },
            {"source": "REQ 1", "target": "REQ-1"},
        ],
    }
    scene = typed_ir_to_scene("requirement", ir)

    assert scene is not None
    texts = list(typed_ir_semantic_texts("requirement", ir, scene))
    assert texts == [
        "REQ_1",
        "Functional Requirement",
        "R-001",
        "User can pay",
        "high",
        "test",
        "REQ_1_2",
        "Requirement",
        "REQ 1",
        "Backup path",
        "medium",
        "analysis",
        "element_REQ_1",
        "system",
        "api.md",
        "satisfies",
        "traces",
    ]
    assert (
        ocr_recall(
            ["Secret heading Concealed primary caption component connector"],
            "",
            generated_texts=texts,
        )
        == 0
    )


def test_eventmodeling_semantic_texts_mirror_lane_typed_frame_and_relation_labels():
    ir = {
        "title": "Hidden accessibility title",
        "lanes": [
            {
                "id": "customer",
                "label": "Customer lane",
                "frames": [
                    {
                        "id": "source_internal",
                        "type": "UI",
                        "time": "https://clock",
                        "label": "style #checkout",
                        "text": "Hidden frame text",
                    }
                ],
            },
            {
                "id": "operations",
                "frames": [
                    {
                        "id": "target_internal",
                        "label": "Order placed",
                    }
                ],
            },
        ],
        "relations": [
            {
                "source": "source_internal",
                "target": "target_internal",
                "label": "continue | retry",
                "text": "Hidden relation text",
            }
        ],
    }
    scene = typed_ir_to_scene("eventmodeling", ir)

    assert scene is not None
    assert [(element.id, element.text) for element in scene.elements] == [
        ("source_internal", "https://clock — [ui] style #checkout"),
        ("target_internal", "[unknown] Order placed"),
    ]
    assert [(group.id, group.label, group.member_ids) for group in scene.groups] == [
        ("lane_customer", "Customer lane", ["source_internal"]),
        ("lane_operations", "operations", ["target_internal"]),
    ]
    assert scene.reading_direction == "LR"
    texts = list(typed_ir_semantic_texts("eventmodeling", ir, scene))
    assert texts == [
        "Customer lane",
        "https://clock — [ui] style #checkout",
        "operations",
        "[unknown] Order placed",
        "continue | retry",
    ]
    assert (
        ocr_recall(
            [
                "Customer lane operations https clock ui style checkout unknown "
                "Order placed continue retry"
            ],
            "",
            generated_texts=texts,
        )
        == 1
    )
    assert ocr_recall(["8203 35 58 124"], "", generated_texts=texts) == 0
    assert (
        ocr_recall(
            ["Concealed accessibility heading payload connector source_internal target_internal"],
            "",
            generated_texts=texts,
        )
        == 0
    )


def test_wardley_semantic_texts_include_native_title_and_visible_labels_only():
    ir = {
        "title": "Payment value chain",
        "description": "Hidden accessibility description",
        "components": [
            {
                "id": "internal_user",
                "label": "Customer",
                "text": "Hidden component text",
                "x": 0.9,
                "y": 0.8,
                "anchor": True,
            },
            UserDict(
                {
                    "id": "payment_api",
                    "text": "Hidden default component text",
                    "x": 0.5,
                    "y": 0.4,
                    "evidence_ids": ["ocr-api"],
                }
            ),
        ],
        "links": [
            {
                "source": "internal_user",
                "target": "payment_api",
                "label": "requests",
                "text": "Hidden link text",
            }
        ],
    }
    scene = typed_ir_to_scene("wardley", ir)

    assert scene is not None
    assert [(element.id, element.text) for element in scene.elements] == [
        ("internal_user", "Customer"),
        ("payment_api", "payment_api"),
    ]
    assert scene.elements[1].evidence_ids == ["ocr-api"]
    texts = list(typed_ir_semantic_texts("wardley", ir, scene))
    assert texts == ["Payment value chain", "Customer", "payment_api", "requests"]
    assert (
        ocr_recall(["Payment value chain Customer payment_api requests"], "", generated_texts=texts)
        == 1
    )
    assert (
        ocr_recall(
            ["Hidden accessibility description component link internal_user anchor"],
            "",
            generated_texts=texts,
        )
        == 0
    )


def test_zenuml_semantic_texts_follow_sequence_fallback_aliases_and_messages():
    ir = {
        "title": "Hidden accessibility title",
        "participants": [
            {"id": "InternalUser", "label": "Customer", "text": "Hidden participant text"},
            UserDict(
                {
                    "id": "PaymentAPI",
                    "text": "Hidden default participant text",
                    "evidence_ids": ["ocr-api"],
                }
            ),
        ],
        "messages": [
            {
                "source": "InternalUser",
                "target": "PaymentAPI",
                "label": "Authorize payment",
                "text": "Hidden message text",
            },
            {
                "source": "InternalUser",
                "target": "PaymentAPI",
                "label": "Authorize payment",
            },
        ],
    }
    scene = typed_ir_to_scene("zenuml", ir)

    assert scene is not None
    assert [(element.id, element.text) for element in scene.elements] == [
        ("InternalUser", "Customer"),
        ("PaymentAPI", "PaymentAPI"),
    ]
    assert scene.elements[1].evidence_ids == ["ocr-api"]
    texts = list(typed_ir_semantic_texts("zenuml", ir, scene))
    assert texts == ["Customer", "PaymentAPI", "Authorize payment", "Authorize payment"]
    assert (
        ocr_recall(
            ["Customer PaymentAPI Authorize payment Authorize payment"],
            "",
            generated_texts=texts,
        )
        == 1
    )
    assert (
        ocr_recall(
            ["Hidden accessibility title participant message InternalUser"],
            "",
            generated_texts=texts,
        )
        == 0
    )


def test_planning_and_event_modeling_scenes_preserve_emitted_elements_and_evidence():
    timeline = typed_ir_to_scene(
        "timeline",
        {"events": [{"id": "launch", "time": "Q1", "label": "Launch", "evidence_ids": ["ocr-1"]}]},
    )
    kanban = typed_ir_to_scene(
        "kanban",
        {
            "columns": [{"id": "todo", "label": "Todo", "evidence_ids": ["ocr-2"]}],
            "cards": [
                {
                    "id": "task",
                    "label": "Ship",
                    "column_id": "todo",
                    "evidence_ids": ["ocr-3"],
                }
            ],
        },
    )
    event_model = typed_ir_to_scene(
        "eventmodeling",
        {
            "lanes": [
                {
                    "frames": [
                        {"id": "submit", "label": "Submit", "evidence_ids": ["ocr-4"]},
                        {"id": "placed", "label": "Placed", "evidence_ids": ["ocr-5"]},
                    ]
                }
            ],
            "relations": [{"source": "submit", "target": "placed", "evidence_ids": ["line-1"]}],
        },
    )

    assert timeline is not None and timeline.elements[0].evidence_ids == ["ocr-1"]
    assert kanban is not None and kanban.relations[0].semantic_relation == "containment"
    assert event_model is not None and event_model.relations[0].evidence_ids == ["line-1"]


def test_hierarchy_lineage_wardley_and_venn_scene_adapters_are_attributable():
    organization = typed_ir_to_scene(
        "organization",
        {
            "root": {
                "id": "ceo",
                "label": "CEO",
                "evidence_ids": ["ocr-ceo"],
                "children": [{"id": "cto", "label": "CTO", "evidence_ids": ["ocr-cto"]}],
            }
        },
    )
    lineage = typed_ir_to_scene(
        "data_lineage",
        {
            "datasets": [{"id": "raw", "evidence_ids": ["ocr-raw"]}],
            "processes": [{"id": "etl", "evidence_ids": ["ocr-etl"]}],
            "relations": [{"source": "raw", "target": "etl", "evidence_ids": ["line-etl"]}],
        },
    )
    wardley = typed_ir_to_scene(
        "wardley",
        {
            "components": [
                {"id": "user", "label": "User", "x": 0.9, "y": 0.7, "evidence_ids": ["ocr-user"]},
                {"id": "api", "label": "API", "x": 0.5, "y": 0.4, "evidence_ids": ["ocr-api"]},
            ],
            "links": [{"source": "user", "target": "api", "evidence_ids": ["line-api"]}],
        },
    )
    venn = typed_ir_to_scene(
        "venn",
        {
            "sets": [
                {"id": "A", "label": "A", "evidence_ids": ["ocr-a"]},
                {"id": "B", "label": "B", "evidence_ids": ["ocr-b"]},
            ],
            "intersections": [{"sets": ["A", "B"], "label": "Both", "evidence_ids": ["ocr-both"]}],
        },
    )

    assert organization is not None and organization.relations[0].target_id == "cto"
    assert lineage is not None and lineage.relations[0].semantic_relation == "unknown"
    assert wardley is not None and wardley.relations[0].evidence_ids == ["line-api"]
    assert venn is not None and len(venn.elements) == 3 and len(venn.relations) == 2


def test_treemap_scene_uses_explicit_recursive_ids_and_child_attribution() -> None:
    scene = typed_ir_to_scene(
        "treemap",
        {
            "root": {
                "id": "portfolio",
                "label": "Portfolio",
                "bbox": [0, 0, 40, 40],
                "evidence_ids": ["contour-portfolio"],
                "children": [
                    {
                        "id": "product",
                        "label": "Product",
                        "value": 4,
                        "bbox": [2, 2, 20, 20],
                        "evidence_ids": ["ocr-product", "contour-product"],
                    }
                ],
            }
        },
    )

    assert scene is not None
    assert [(item.id, item.bbox, item.evidence_ids) for item in scene.elements] == [
        ("portfolio", (0, 0, 40, 40), ["contour-portfolio"]),
        ("product", (2, 2, 20, 20), ["ocr-product", "contour-product"]),
    ]
    assert [
        (
            relation.source_id,
            relation.target_id,
            relation.semantic_relation,
            relation.evidence_ids,
        )
        for relation in scene.relations
    ] == [("portfolio", "product", "containment", ["ocr-product", "contour-product"])]


def test_venn_scene_uses_explicit_intersection_id_geometry_and_attribution() -> None:
    scene = typed_ir_to_scene(
        "venn",
        {
            "sets": [
                {
                    "id": "A",
                    "label": "Set A",
                    "bbox": [0, 0, 20, 20],
                    "evidence_ids": ["contour-a"],
                },
                {
                    "id": "B",
                    "label": "Set B",
                    "bbox": [10, 0, 30, 20],
                    "evidence_ids": ["contour-b"],
                },
            ],
            "intersections": [
                {
                    "id": "both",
                    "sets": ["A", "B"],
                    "label": "Both",
                    "value": 2,
                    "bbox": [10, 2, 20, 18],
                    "evidence_ids": ["ocr-both", "contour-both"],
                }
            ],
        },
    )

    assert scene is not None
    assert [(item.id, item.bbox, item.evidence_ids) for item in scene.elements] == [
        ("A", (0, 0, 20, 20), ["contour-a"]),
        ("B", (10, 0, 30, 20), ["contour-b"]),
        ("both", (10, 2, 20, 18), ["ocr-both", "contour-both"]),
    ]
    assert [
        (relation.source_id, relation.target_id, relation.evidence_ids)
        for relation in scene.relations
    ] == [
        ("A", "both", ["ocr-both", "contour-both"]),
        ("B", "both", ["ocr-both", "contour-both"]),
    ]


@pytest.mark.parametrize(
    ("diagram_type", "ir"),
    [
        (
            "treemap",
            {
                "root": {
                    "id": "same",
                    "label": "Root",
                    "children": [{"id": "same", "label": "Leaf", "value": 1}],
                }
            },
        ),
        (
            "venn",
            {
                "sets": [{"id": "A", "label": "A"}, {"id": "B", "label": "B"}],
                "intersections": [{"id": "A", "sets": ["A", "B"], "label": "Both"}],
            },
        ),
    ],
)
def test_treemap_and_venn_scenes_fail_closed_on_duplicate_attribution_ids(
    diagram_type: str, ir: dict[str, object]
) -> None:
    assert typed_ir_to_scene(diagram_type, ir) is None


def test_phase2_sources_keep_requested_structure_even_when_code_falls_back():
    deployment = typed_ir_to_scene(
        "deployment",
        {
            "nodes": [{"id": "app"}, {"id": "db"}],
            "artifacts": [{"id": "binary"}],
            "links": [{"source": "app", "target": "db"}],
        },
    )
    er = typed_ir_to_scene(
        "er",
        {
            "entities": [{"id": "customer"}, {"id": "order"}],
            "relationships": [{"source": "customer", "target": "order"}],
        },
    )

    assert deployment is not None and len(deployment.elements) == 3
    assert deployment.relations[0].arrow_at_end
    assert er is not None and not er.relations[0].arrow_at_end
