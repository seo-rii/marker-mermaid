from collections import UserDict

import pytest

from marker_mermaid.candidate_scene import typed_ir_semantic_texts, typed_ir_to_scene
from marker_mermaid.models import (
    MAX_EVIDENCE_REFS,
    MAX_SCENE_ELEMENTS,
    MAX_SCENE_GROUPS,
    MAX_SCENE_RELATIONS,
    DiagramSceneIR,
    SceneElement,
)
from marker_mermaid.quality import relative_layout_similarity
from marker_mermaid.scoring import ocr_recall
from marker_mermaid.serializers import (
    SerializationError,
    serialize_architecture,
    serialize_architecture_flowchart_fallback,
    serialize_gantt,
    serialize_sequence,
)
from marker_mermaid.serializers_phase2 import serialize_phase2
from marker_mermaid.serializers_uml import serialize_state


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


def test_sequence_scene_and_semantic_texts_include_unreadable_message_fallback() -> None:
    ir = {
        "participants": ["Client", "API"],
        "messages": [
            {
                "source": "Client",
                "target": "API",
                "text": "Hidden message alias",
            },
            {"source": "API", "target": "Client", "label": "Reply"},
        ],
    }

    scene = typed_ir_to_scene("sequence", ir)
    code = serialize_sequence(ir)

    assert scene is not None
    assert "Hidden message alias" not in code
    assert "Client->>API: [unreadable]" in code
    assert [relation.label for relation in scene.relations] == ["[unreadable]", "Reply"]
    assert list(typed_ir_semantic_texts("sequence", ir, scene)) == [
        "Client",
        "API",
        "[unreadable]",
        "Reply",
    ]
    assert (
        ocr_recall(
            ["Hidden message alias"],
            "",
            generated_texts=typed_ir_semantic_texts("sequence", ir, scene),
        )
        == 0
    )


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


def test_state_scene_excludes_pseudostate_labels_but_keeps_visible_state_text() -> None:
    ir = {
        "states": [
            {
                "id": "idle",
                "label": "Idle",
                "kind": "state",
                "evidence_ids": ["ocr-idle"],
            },
            {
                "id": "decision",
                "label": "Ignored choice label",
                "kind": "choice",
                "evidence_ids": ["shape-choice"],
            },
            {
                "id": "parallel",
                "label": "Ignored fork label",
                "kind": "fork",
                "evidence_ids": ["shape-fork"],
            },
            {
                "id": "joined",
                "label": "Ignored join label",
                "kind": "join",
                "evidence_ids": ["shape-join"],
            },
            {
                "id": "unlabeled",
                "text": "Ignored normal-state text alias",
                "kind": "state",
                "evidence_ids": ["shape-unlabeled"],
            },
        ],
        "transitions": [
            {
                "source": "idle",
                "target": "decision",
                "label": "Choose",
                "evidence_ids": ["arrow-choose"],
            },
            {
                "source": "decision",
                "target": "parallel",
                "evidence_ids": ["arrow-fork"],
            },
            {
                "source": "parallel",
                "target": "joined",
                "label": "Complete",
                "evidence_ids": ["arrow-complete"],
            },
        ],
    }

    scene = typed_ir_to_scene("state", ir)
    code = serialize_state(ir)

    assert scene is not None
    assert "state decision <<choice>>" in code
    assert "state parallel <<fork>>" in code
    assert "state joined <<join>>" in code
    assert 'state "unlabeled" as unlabeled' in code
    assert [(element.id, element.text) for element in scene.elements] == [
        ("idle", "Idle"),
        ("decision", None),
        ("parallel", None),
        ("joined", None),
        ("unlabeled", "unlabeled"),
    ]
    texts = list(typed_ir_semantic_texts("state", ir, scene))
    assert texts == ["Idle", "unlabeled", "Choose", "Complete"]
    assert ocr_recall(["Idle unlabeled Choose Complete"], "", generated_texts=texts) == 1
    assert (
        ocr_recall(
            ["Ignored choice label fork join normal-state text alias"],
            "",
            generated_texts=texts,
        )
        == 0
    )


def test_state_scene_uses_serializer_emitted_ids_and_transition_endpoints() -> None:
    ir = {
        "direction": "LR",
        "states": [
            {
                "id": "A-B",
                "label": "Alpha",
                "evidence_ids": ["ocr-alpha"],
            },
            {
                "id": "C D",
                "label": "Charlie",
                "evidence_ids": ["ocr-charlie"],
            },
        ],
        "transitions": [
            {
                "source": "A-B",
                "target": "C D",
                "label": "Advance",
                "evidence_ids": ["arrow-advance"],
            }
        ],
    }

    scene = typed_ir_to_scene("state", ir)
    code = serialize_state(ir)

    assert scene is not None
    assert scene.reading_direction == "LR"
    assert [(element.id, element.text, element.evidence_ids) for element in scene.elements] == [
        ("A_B", "Alpha", ["ocr-alpha"]),
        ("C_D", "Charlie", ["ocr-charlie"]),
    ]
    assert 'state "Alpha" as A_B' in code
    assert 'state "Charlie" as C_D' in code
    assert [
        (
            relation.id,
            relation.source_id,
            relation.target_id,
            relation.label,
            relation.evidence_ids,
        )
        for relation in scene.relations
    ] == [("state_transition_1", "A_B", "C_D", "Advance", ["arrow-advance"])]
    assert "A_B --> C_D : Advance" in code
    assert list(typed_ir_semantic_texts("state", ir, scene)) == [
        "Alpha",
        "Charlie",
        "Advance",
    ]


@pytest.mark.parametrize(
    ("transitions", "message"),
    [
        ("not-a-list", "requires a list"),
        (["not-an-object"], "items must be objects"),
        ([{"source": "A", "target": "A"}], "requires at least one evidence id"),
        (
            [
                {
                    "source": "A",
                    "target": "missing",
                    "evidence_ids": ["arrow-missing"],
                }
            ],
            "unknown endpoint",
        ),
    ],
)
def test_state_scene_and_serializer_fail_closed_on_invalid_transitions(
    transitions: object,
    message: str,
) -> None:
    ir = {
        "states": [{"id": "A", "label": "Active", "evidence_ids": ["ocr-active"]}],
        "transitions": transitions,
    }

    with pytest.raises(SerializationError, match=message):
        serialize_state(ir)
    assert typed_ir_to_scene("state", ir) is None


def test_state_boundary_transitions_serialize_without_scene_relations() -> None:
    ir = {
        "states": [{"id": "active", "label": "Active", "evidence_ids": ["ocr-active"]}],
        "transitions": [
            {
                "source": "[*]",
                "target": "active",
                "label": "Enter",
                "evidence_ids": ["arrow-enter"],
            },
            {
                "source": "active",
                "target": "[*]",
                "label": "Exit",
                "evidence_ids": ["arrow-exit"],
            },
            {
                "source": "active",
                "target": "active",
                "label": "Retry",
                "evidence_ids": ["arrow-retry"],
            },
        ],
    }

    scene = typed_ir_to_scene("state", ir)
    code = serialize_state(ir)

    assert "[*] --> active : Enter" in code
    assert "active --> [*] : Exit" in code
    assert scene is not None
    assert [
        (relation.id, relation.source_id, relation.target_id, relation.label)
        for relation in scene.relations
    ] == [("state_transition_3", "active", "active", "Retry")]
    assert scene.relations[0].evidence_ids == ["arrow-retry"]
    assert list(typed_ir_semantic_texts("state", ir, scene)) == [
        "Active",
        "Enter",
        "Exit",
        "Retry",
    ]


@pytest.mark.parametrize(
    "states",
    [
        [{"id": "unsupported", "kind": "history"}],
        ["not-a-state-record"],
    ],
)
def test_state_scene_fails_closed_on_malformed_or_unsupported_records(states: list[object]) -> None:
    assert typed_ir_to_scene("state", {"states": states, "transitions": []}) is None


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


def test_gantt_scene_uses_serializer_task_fallback_numbering_per_section() -> None:
    ir = {
        "sections": [
            {
                "id": "build",
                "title": "Build",
                "tasks": [
                    {
                        "id": "internal-first",
                        "text": "Hidden task alias",
                        "start": "2026-07-01",
                        "end": "2026-07-02",
                    },
                    {
                        "id": "named",
                        "label": "Named task",
                        "start": "2026-07-02",
                        "end": "2026-07-03",
                    },
                ],
            },
            {
                "id": "ship",
                "title": "Ship",
                "tasks": [
                    {
                        "id": "internal-third",
                        "start": "2026-07-03",
                        "end": "2026-07-04",
                    }
                ],
            },
        ]
    }

    scene = typed_ir_to_scene("gantt", ir)
    code = serialize_gantt(ir)

    assert scene is not None
    assert "\n    Hidden task alias :" not in code
    assert "Task 1 :internal-first" in code
    assert [(element.id, element.text) for element in scene.elements] == [
        ("internal-first", "Task 1"),
        ("named", "Named task"),
        ("internal-third", "Task 1"),
    ]
    assert list(typed_ir_semantic_texts("gantt", ir, scene)) == [
        "Build",
        "Task 1",
        "Named task",
        "Ship",
        "Task 1",
    ]


def test_gantt_scene_preserves_duplicate_source_ids_with_collision_free_attribution() -> None:
    ir = {
        "sections": [
            {
                "id": "phase",
                "title": "First phase",
                "tasks": [
                    {
                        "id": "shared-task",
                        "label": "First task",
                        "start": "2026-07-01",
                        "end": "2026-07-02",
                        "evidence_ids": ["ocr-first"],
                    },
                    {
                        "id": "shared-task",
                        "label": "Second task",
                        "start": "2026-07-02",
                        "end": "2026-07-03",
                        "evidence_ids": ["ocr-second"],
                    },
                ],
            },
            {
                "id": "phase",
                "tasks": [
                    {
                        "id": "shared-task",
                        "start": "2026-07-03",
                        "end": "2026-07-04",
                        "evidence_ids": ["ocr-third"],
                    },
                    {
                        "id": "shared-task",
                        "label": "Fourth task",
                        "start": "2026-07-04",
                        "end": "2026-07-05",
                        "evidence_ids": ["ocr-fourth"],
                    },
                ],
            },
        ]
    }

    scene = typed_ir_to_scene("gantt", ir)
    code = serialize_gantt(ir)

    assert scene is not None
    assert code.count(":shared-task,") == 4
    assert "gantt_task_" not in code
    assert "gantt_section_" not in code
    assert [(element.id, element.text, element.evidence_ids) for element in scene.elements] == [
        ("shared-task", "First task", ["ocr-first"]),
        ("gantt_task_1_2", "Second task", ["ocr-second"]),
        ("gantt_task_2_1", "Task 1", ["ocr-third"]),
        ("gantt_task_2_2", "Fourth task", ["ocr-fourth"]),
    ]
    assert len({element.id for element in scene.elements}) == 4
    assert [(group.id, group.label, group.member_ids) for group in scene.groups] == [
        ("phase", "First phase", ["shared-task", "gantt_task_1_2"]),
        ("gantt_section_2", "Tasks", ["gantt_task_2_1", "gantt_task_2_2"]),
    ]
    assert len({group.id for group in scene.groups}) == 2
    assert list(typed_ir_semantic_texts("gantt", ir, scene)) == [
        "First phase",
        "First task",
        "Second task",
        "Tasks",
        "Task 1",
        "Fourth task",
    ]


def test_block_scene_uses_serializer_ids_labels_and_mapped_endpoints() -> None:
    ir = {
        "blocks": [
            {
                "id": "A-B",
                "evidence_ids": ["ocr-unreadable"],
            },
            {
                "id": "A B",
                "label": "Visible block",
                "evidence_ids": ["ocr-visible"],
            },
        ],
        "edges": [
            {
                "source": "A-B",
                "target": "A B",
                "label": "Next",
                "evidence_ids": ["arrow-next"],
            }
        ],
    }

    scene = typed_ir_to_scene("block", ir)
    code = serialize_phase2("block", ir)[0]

    assert scene is not None
    assert [(element.id, element.text, element.evidence_ids) for element in scene.elements] == [
        ("A_B", "[unreadable]", ["ocr-unreadable"]),
        ("A_B_2", "Visible block", ["ocr-visible"]),
    ]
    assert 'A_B["[unreadable]"]' in code
    assert 'A_B_2["Visible block"]' in code
    assert [
        (relation.source_id, relation.target_id, relation.label) for relation in scene.relations
    ] == [("A_B", "A_B_2", "Next")]
    assert scene.relations[0].evidence_ids == ["arrow-next"]
    assert list(typed_ir_semantic_texts("block", ir, scene)) == [
        "[unreadable]",
        "Visible block",
        "Next",
    ]


def test_block_scene_fails_closed_on_unknown_edge_endpoint() -> None:
    ir = {
        "blocks": [{"id": "known", "label": "Known"}],
        "edges": [{"source": "known", "target": "missing"}],
    }

    with pytest.raises(SerializationError, match="unknown endpoint"):
        serialize_phase2("block", ir)
    assert typed_ir_to_scene("block", ir) is None


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


def test_eventmodeling_scene_matches_flowchart_fallback_without_source_extras():
    ir = {
        "direction": "RL",
        "title": "Hidden accessibility title",
        "lanes": [
            {
                "id": "customer-lane",
                "label": 'Customer "lane";',
                "bbox": [1, 2, 30, 40],
                "role": "raw lane role",
                "evidence_ids": ["lane-only"],
                "frames": [
                    {
                        "id": "open-checkout",
                        "type": "UI",
                        "time": "T0;",
                        "label": 'Open "checkout"\\screen;',
                        "text": "Hidden frame text",
                        "bbox": [5, 6, 20, 22],
                        "role": "raw frame role",
                        "shape": "diamond",
                        "style": "dashed",
                        "evidence_ids": ["ocr-open"],
                    }
                ],
            },
            {
                "id": "operations",
                "bbox": [50, 2, 90, 40],
                "frames": [
                    {
                        "id": "order-placed",
                        "label": "Order placed",
                        "bbox": [55, 6, 80, 22],
                        "evidence_ids": ["ocr-placed"],
                    }
                ],
            },
        ],
        "relations": [
            {
                "id": "raw-relation-id",
                "source": "open-checkout",
                "target": "order-placed",
                "label": "continue | retry;",
                "text": "Hidden relation text",
                "style": "dashed",
                "bidirectional": True,
                "arrow_at_start": True,
                "arrow_at_end": False,
                "semantic_relation": "causal",
                "evidence_ids": ["line-continue"],
            }
        ],
    }
    scene = typed_ir_to_scene("eventmodeling", ir)

    assert scene is not None
    assert [(element.id, element.text) for element in scene.elements] == [
        (
            "eventmodeling_frame_open_checkout",
            "T0; — [ui] Open ″checkout″∖screen;",
        ),
        ("eventmodeling_frame_order_placed", "[unknown] Order placed"),
    ]
    assert [element.role for element in scene.elements] == ["node", "node"]
    assert [element.bbox for element in scene.elements] == [
        (0.0, 0.0, 0.0, 0.0),
        (0.0, 0.0, 0.0, 0.0),
    ]
    assert [element.shape for element in scene.elements] == [None, None]
    assert [element.evidence_ids for element in scene.elements] == [
        ["ocr-open"],
        ["ocr-placed"],
    ]
    assert [(group.id, group.label, group.member_ids) for group in scene.groups] == [
        (
            "eventmodeling_lane_customer_lane",
            "Customer ″lane″;",
            ["eventmodeling_frame_open_checkout"],
        ),
        (
            "eventmodeling_lane_operations",
            "operations",
            ["eventmodeling_frame_order_placed"],
        ),
    ]
    assert [group.role for group in scene.groups] == ["lane", "lane"]
    assert [group.bbox for group in scene.groups] == [
        (0.0, 0.0, 0.0, 0.0),
        (0.0, 0.0, 0.0, 0.0),
    ]
    assert len(scene.relations) == 1
    relation = scene.relations[0]
    assert relation.id == "eventmodeling_relation_1"
    assert relation.source_id == "eventmodeling_frame_open_checkout"
    assert relation.target_id == "eventmodeling_frame_order_placed"
    assert relation.label == "continue ∣ retry⁏"
    assert relation.relation_type == "generated_connector"
    assert relation.semantic_relation == "sequence"
    assert not relation.arrow_at_start and relation.arrow_at_end
    assert relation.polyline == []
    assert relation.line_style is None
    assert relation.evidence_ids == ["line-continue"]
    assert scene.reading_direction == "LR"
    assert scene.diagram_type_candidates == ["eventmodeling"]
    assert scene.coordinate_space == "pixels"
    texts = list(typed_ir_semantic_texts("eventmodeling", ir, scene))
    assert texts == [
        "Customer ″lane″;",
        "T0; — [ui] Open ″checkout″∖screen;",
        "operations",
        "[unknown] Order placed",
        "continue ∣ retry⁏",
    ]
    assert (
        ocr_recall(
            [
                "Customer lane T0 ui Open checkout screen operations unknown "
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
            [
                "Hidden accessibility title concealed payload metadata dashed diamond "
                "raw-relation-id"
            ],
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


def test_wardley_scene_uses_only_emitted_structure_and_native_coordinate_orientation():
    ir = {
        "direction": "RL",
        "components": [
            {
                "id": "user",
                "label": "User",
                "x": 0.1,
                "y": 0.2,
                "anchor": True,
                "bbox": [10, 20, 30, 40],
                "role": "source-only-role",
                "shape": "diamond",
                "evidence_ids": ["ocr-user"],
            },
            {
                "id": "api",
                "label": "API",
                "x": 0.8,
                "y": 0.9,
                "bbox": [60, 70, 90, 95],
                "evidence_ids": ["ocr-api"],
            },
        ],
        "links": [
            {
                "source": "user",
                "target": "api",
                "label": "uses",
                "bidirectional": True,
                "arrow_at_start": True,
                "style": "dashed",
                "semantic_relation": "causal",
                "evidence_ids": ["line-user-api"],
            }
        ],
    }

    scene = typed_ir_to_scene("wardley", ir)

    assert scene is not None
    assert scene.coordinate_space == "normalized"
    assert scene.reading_direction == "unknown"
    assert [(item.id, item.role, item.text, item.bbox) for item in scene.elements] == [
        ("user", "anchor", "User", (0.1, 0.8, 0.1, 0.8)),
        ("api", "component", "API", (0.8, pytest.approx(0.1), 0.8, pytest.approx(0.1))),
    ]
    assert [item.shape for item in scene.elements] == [None, None]
    assert [item.evidence_ids for item in scene.elements] == [["ocr-user"], ["ocr-api"]]
    [link] = scene.relations
    assert (link.source_id, link.target_id, link.label) == ("user", "api", "uses")
    assert not link.arrow_at_start
    assert not link.arrow_at_end
    assert link.line_style is None
    assert link.semantic_relation == "unknown"
    assert link.evidence_ids == ["line-user-api"]


def test_wardley_layout_score_uses_emitted_xy_not_source_bbox_metadata():
    source = DiagramSceneIR(
        elements=[
            SceneElement(id="a", role="component", text="A", bbox=(0, 0, 10, 10)),
            SceneElement(id="b", role="component", text="B", bbox=(90, 90, 100, 100)),
        ]
    )
    generated = typed_ir_to_scene(
        "wardley",
        {
            "components": [
                {
                    "id": "a",
                    "label": "A",
                    "x": 0.9,
                    "y": 0.1,
                    "bbox": [0, 0, 10, 10],
                },
                {
                    "id": "b",
                    "label": "B",
                    "x": 0.1,
                    "y": 0.9,
                    "bbox": [90, 90, 100, 100],
                },
            ]
        },
    )

    assert generated is not None
    assert relative_layout_similarity(source, generated).value == 0


def test_wardley_scene_coordinates_match_native_token_rounding() -> None:
    scene = typed_ir_to_scene(
        "wardley",
        {
            "components": [
                {"id": "a", "x": 0.5000000000000001, "y": 0.2},
                {"id": "b", "x": 0.5000000000000002, "y": 0.8},
            ]
        },
    )

    assert scene is not None
    assert scene.elements[0].bbox[0] == scene.elements[1].bbox[0] == 0.5


def test_wardley_flowchart_fallback_scene_drops_layout_and_anchor_semantics() -> None:
    ir = {
        "title": "Value map title is not visible in the fallback canvas",
        "components": [
            {
                "id": "user",
                "label": 'User "one"',
                "x": 0.1,
                "y": 0.2,
                "anchor": True,
                "bbox": [10, 20, 30, 40],
                "evidence_ids": ["ocr-user"],
            },
            {
                "id": "api",
                "label": "API \\ service",
                "x": 0.8,
                "y": 0.9,
                "bbox": [60, 70, 90, 95],
                "evidence_ids": ["ocr-api"],
            },
        ],
        "links": [
            {
                "source": "user",
                "target": "api",
                "label": "uses | retry",
                "evidence_ids": ["line-user-api"],
            }
        ],
    }

    scene = typed_ir_to_scene("wardley", ir, emitted_diagram_type="flowchart")

    assert scene is not None
    assert scene.coordinate_space == "pixels"
    assert scene.reading_direction == "LR"
    assert [(element.id, element.role, element.text) for element in scene.elements] == [
        ("wardley_component_1", "node", "User ″one″"),
        ("wardley_component_2", "node", "API ∖ service"),
    ]
    assert all(element.shape == "rectangle" for element in scene.elements)
    assert all(element.bbox == (0, 0, 0, 0) for element in scene.elements)
    assert [element.evidence_ids for element in scene.elements] == [
        ["ocr-user"],
        ["ocr-api"],
    ]
    [link] = scene.relations
    assert link.id == "wardley_link_1"
    assert (link.source_id, link.target_id, link.label) == (
        "wardley_component_1",
        "wardley_component_2",
        "uses ∣ retry",
    )
    assert not link.arrow_at_start
    assert not link.arrow_at_end
    assert link.evidence_ids == ["line-user-api"]
    assert list(typed_ir_semantic_texts("wardley", ir, scene)) == [
        "User ″one″",
        "API ∖ service",
        "uses ∣ retry",
    ]


def test_cynefin_scene_uses_reserved_plan_ids_groups_and_transition_only_relations():
    ir = {
        "title": "Hidden accessibility title",
        "description": "Hidden accessibility description",
        "direction": "RL",
        "domains": [
            {
                "name": "complex",
                "bbox": [0, 0, 40, 40],
                "evidence_ids": ["domain-complex"],
                "items": [
                    {
                        "id": "hidden-item-id",
                        "label": "Probe &quot; safely",
                        "text": "Hidden item text",
                        "bbox": [1, 1, 10, 10],
                        "evidence_ids": ["item-probe"],
                    },
                    {"label": "Observe", "evidence_ids": ["item-observe"]},
                ],
            },
            {
                "name": "clear",
                "evidence_ids": ["domain-clear"],
                "items": [{"label": "Respond", "evidence_ids": ["item-respond"]}],
            },
        ],
        "transitions": [
            {
                "id": "hidden-transition-id",
                "source": "complex",
                "target": "clear",
                "label": "stabilize",
                "style": "dashed",
                "bidirectional": True,
                "evidence_ids": ["transition-stabilize"],
            }
        ],
    }

    scene = typed_ir_to_scene("cynefin", ir)

    assert scene is not None
    assert scene.coordinate_space == "pixels"
    assert scene.reading_direction == "unknown"
    domains = [item for item in scene.elements if item.role == "domain"]
    items = [item for item in scene.elements if item.role == "item"]
    runtime_template = [item for item in scene.elements if item.role == "runtime_template"]
    assert [item.text for item in domains] == [
        "Complex",
        "Complicated",
        "Chaotic",
        "Clear",
        "Confusion",
    ]
    assert [item.text for item in runtime_template] == [
        "Probe → Sense → Respond",
        "Emergent Practices",
        "Sense → Analyse → Respond",
        "Good Practices",
        "Act → Sense → Respond",
        "Novel Practices",
        "Sense → Categorise → Respond",
        "Best Practices",
        "Disorder",
    ]
    assert [item.text for item in items] == ["Probe ＆quot; safely", "Observe", "Respond"]
    assert all(item.id.startswith("cynefin_domain_") for item in domains)
    assert all(item.id.startswith("cynefin_item_") for item in items)
    assert all(item.bbox == (0.0, 0.0, 0.0, 0.0) for item in scene.elements)
    assert [item.evidence_ids for item in domains] == [
        ["domain-complex"],
        [],
        [],
        ["domain-clear"],
        [],
    ]
    assert all(not item.evidence_ids for item in runtime_template)
    assert [item.evidence_ids for item in items] == [
        ["item-probe"],
        ["item-observe"],
        ["item-respond"],
    ]
    assert len(scene.groups) == 2
    assert all(group.bbox == (0.0, 0.0, 0.0, 0.0) for group in scene.groups)
    assert scene.groups[0].member_ids == [item.id for item in items[:2]]
    assert scene.groups[1].member_ids == [items[2].id]
    [transition] = scene.relations
    assert transition.source_id == domains[0].id
    assert transition.target_id == domains[3].id
    assert transition.label == "stabilize"
    assert not transition.arrow_at_start
    assert transition.arrow_at_end
    assert transition.line_style is None
    assert transition.semantic_relation == "unknown"
    assert transition.evidence_ids == ["transition-stabilize"]
    texts = list(typed_ir_semantic_texts("cynefin", ir, scene))
    assert texts == [
        "Complex",
        "Complicated",
        "Chaotic",
        "Clear",
        "Confusion",
        "Probe → Sense → Respond",
        "Emergent Practices",
        "Sense → Analyse → Respond",
        "Good Practices",
        "Act → Sense → Respond",
        "Novel Practices",
        "Sense → Categorise → Respond",
        "Best Practices",
        "Disorder",
        "Probe ＆quot; safely",
        "Observe",
        "Respond",
        "stabilize",
    ]
    assert (
        ocr_recall(
            [" ".join(texts)],
            "",
            generated_texts=texts,
        )
        == 1
    )
    assert (
        ocr_recall(
            [
                "Hidden accessibility title description item text hidden-item-id "
                "hidden-transition-id dashed bidirectional"
            ],
            "",
            generated_texts=texts,
        )
        == 0
    )


def test_cynefin_scene_matches_runtime_confusion_item_summary() -> None:
    ir = {
        "domains": [
            {
                "name": "confusion",
                "items": [
                    {"label": label, "evidence_ids": [f"ocr-{label.casefold()}"]}
                    for label in ["One", "Two", "Three", "Four", "Five"]
                ],
            }
        ]
    }

    scene = typed_ir_to_scene("cynefin", ir)

    assert scene is not None
    visible_items = [item for item in scene.elements if item.role == "item"]
    assert [item.text for item in visible_items] == ["One", "Two", "Three"]
    summary = next(item for item in scene.elements if item.id == "cynefin_runtime_confusion_more")
    assert summary.role == "runtime_template"
    assert summary.text == "+2 more"
    assert summary.evidence_ids == []
    assert scene.groups[0].member_ids == [
        *(item.id for item in visible_items),
        summary.id,
    ]
    texts = list(typed_ir_semantic_texts("cynefin", ir, scene))
    assert texts[-4:] == ["One", "Two", "Three", "+2 more"]
    assert "Four" not in texts
    assert "Five" not in texts


def test_cynefin_flowchart_scene_preserves_explicit_domains_items_and_transition_only() -> None:
    ir = {
        "domains": [
            {
                "name": "complex",
                "evidence_ids": ["domain-complex"],
                "items": [{"label": "Emergent", "evidence_ids": ["item-complex-1"]}],
            },
            {
                "name": "complicated",
                "evidence_ids": ["domain-complicated"],
                "items": [{"label": "Expert", "evidence_ids": ["item-complicated-1"]}],
            },
            {
                "name": "chaotic",
                "evidence_ids": ["domain-chaotic"],
                "items": [{"label": "Crisis", "evidence_ids": ["item-chaotic-1"]}],
            },
            {
                "name": "clear",
                "evidence_ids": ["domain-clear"],
                "items": [{"label": "Known", "evidence_ids": ["item-clear-1"]}],
            },
            {
                "name": "confusion",
                "evidence_ids": ["domain-confusion"],
                "items": [
                    {
                        "label": label,
                        "evidence_ids": [f"item-confusion-{index}"],
                    }
                    for index, label in enumerate(["One", "Two", "Three", "Four", "Five"], start=1)
                ],
            },
        ],
        "transitions": [
            {
                "source": "complex",
                "target": "clear",
                "label": "stabilize | now",
                "evidence_ids": ["transition-stabilize"],
            }
        ],
    }

    scene = typed_ir_to_scene("cynefin", ir, emitted_diagram_type="flowchart-v2")

    assert scene is not None
    assert scene.coordinate_space == "pixels"
    assert scene.reading_direction == "LR"
    assert all(element.bbox == (0, 0, 0, 0) for element in scene.elements)
    domains = [element for element in scene.elements if element.role == "domain"]
    items = [element for element in scene.elements if element.role == "item"]
    assert [(element.id, element.text) for element in domains] == [
        ("cynefin_domain_complex", "Complex"),
        ("cynefin_domain_complicated", "Complicated"),
        ("cynefin_domain_chaotic", "Chaotic"),
        ("cynefin_domain_clear", "Clear"),
        ("cynefin_domain_confusion", "Confusion"),
    ]
    assert [element.text for element in items] == [
        "Emergent",
        "Expert",
        "Crisis",
        "Known",
        "One",
        "Two",
        "Three",
        "Four",
        "Five",
    ]
    assert all(element.shape == "rectangle" for element in items)
    assert not any(element.role == "runtime_template" for element in scene.elements)
    assert all("cynefin_runtime" not in element.id for element in scene.elements)
    assert [element.evidence_ids for element in domains] == [
        ["domain-complex"],
        ["domain-complicated"],
        ["domain-chaotic"],
        ["domain-clear"],
        ["domain-confusion"],
    ]
    assert len(scene.groups) == 5
    assert [group.id for group in scene.groups] == [element.id for element in domains]
    assert scene.groups[-1].member_ids == [
        f"cynefin_item_confusion_{index}" for index in range(1, 6)
    ]
    [transition] = scene.relations
    assert transition.id == "cynefin_transition_1"
    assert (transition.source_id, transition.target_id, transition.label) == (
        "cynefin_domain_complex",
        "cynefin_domain_clear",
        "stabilize ∣ now",
    )
    assert not transition.arrow_at_start
    assert transition.arrow_at_end
    assert transition.evidence_ids == ["transition-stabilize"]
    texts = list(typed_ir_semantic_texts("cynefin", ir, scene))
    assert texts == [
        "Complex",
        "Emergent",
        "Complicated",
        "Expert",
        "Chaotic",
        "Crisis",
        "Clear",
        "Known",
        "Confusion",
        "One",
        "Two",
        "Three",
        "Four",
        "Five",
        "stabilize ∣ now",
    ]
    assert texts.count("Complex") == 1
    assert "+2 more" not in texts


@pytest.mark.parametrize(
    ("diagram_type", "ir"),
    [
        (
            "wardley",
            {
                "components": [
                    {"id": "same", "label": "A", "x": 0.1, "y": 0.1},
                    {"id": "same", "label": "B", "x": 0.2, "y": 0.2},
                ]
            },
        ),
        (
            "cynefin",
            {
                "domains": [
                    {"name": "complex", "items": ["A"]},
                    {"name": "complex", "items": ["B"]},
                ]
            },
        ),
        (
            "cynefin",
            {"domains": [{"name": "complex", "items": ["A"] * 501}]},
        ),
    ],
)
def test_wardley_and_cynefin_scene_planning_failures_are_isolated(
    diagram_type: str,
    ir: dict[str, object],
) -> None:
    assert typed_ir_to_scene(diagram_type, ir) is None


def test_zenuml_scene_matches_sequence_fallback_without_source_extras():
    ir = {
        "direction": "RL",
        "title": "Hidden accessibility title",
        "participants": [
            {
                "id": "InternalUser",
                "label": "Customer #1; payer",
                "text": "Hidden participant text",
                "bbox": [1, 2, 10, 12],
                "role": "raw participant role",
                "shape": "diamond",
                "style": "dashed",
                "evidence_ids": ["ocr-user"],
            },
            UserDict(
                {
                    "id": "PaymentAPI",
                    "text": "Hidden default participant text",
                    "bbox": [20, 2, 30, 12],
                    "evidence_ids": ["ocr-api"],
                }
            ),
        ],
        "messages": [
            {
                "id": "raw-message-id",
                "source": "InternalUser",
                "target": "PaymentAPI",
                "label": "Authorize #card; now",
                "text": "Hidden message text",
                "bbox": [10, 4, 20, 6],
                "style": "dashed",
                "bidirectional": True,
                "arrow_at_start": True,
                "arrow_at_end": False,
                "semantic_relation": "causal",
                "evidence_ids": ["arrow-message"],
            },
        ],
    }
    scene = typed_ir_to_scene("zenuml", ir)

    assert scene is not None
    assert [(element.id, element.text) for element in scene.elements] == [
        ("zenuml_participant_InternalUser", "Customer ＃1⁏ payer"),
        ("zenuml_participant_PaymentAPI", "PaymentAPI"),
    ]
    assert [element.role for element in scene.elements] == ["participant", "participant"]
    assert [element.bbox for element in scene.elements] == [
        (0.0, 0.0, 0.0, 0.0),
        (0.0, 0.0, 0.0, 0.0),
    ]
    assert [element.shape for element in scene.elements] == [None, None]
    assert [element.evidence_ids for element in scene.elements] == [
        ["ocr-user"],
        ["ocr-api"],
    ]
    assert scene.groups == []
    assert len(scene.relations) == 1
    relation = scene.relations[0]
    assert relation.id == "zenuml_message_1"
    assert relation.source_id == "zenuml_participant_InternalUser"
    assert relation.target_id == "zenuml_participant_PaymentAPI"
    assert relation.label == "Authorize ＃card⁏ now"
    assert relation.relation_type == "message"
    assert relation.semantic_relation == "message"
    assert not relation.arrow_at_start and relation.arrow_at_end
    assert relation.polyline == []
    assert relation.line_style is None
    assert relation.evidence_ids == ["arrow-message"]
    assert scene.reading_direction == "LR"
    assert scene.diagram_type_candidates == ["zenuml"]
    assert scene.coordinate_space == "pixels"
    texts = list(typed_ir_semantic_texts("zenuml", ir, scene))
    assert texts == ["Customer ＃1⁏ payer", "PaymentAPI", "Authorize ＃card⁏ now"]
    assert (
        ocr_recall(
            ["Customer 1 payer PaymentAPI Authorize card now"],
            "",
            generated_texts=texts,
        )
        == 1
    )
    assert (
        ocr_recall(
            [
                "Hidden accessibility title participant message InternalUser raw-message-id "
                "dashed diamond causal"
            ],
            "",
            generated_texts=texts,
        )
        == 0
    )


@pytest.mark.parametrize(
    ("diagram_type", "ir"),
    [
        (
            "eventmodeling",
            {
                "lanes": [{"frames": [{"id": "known", "label": "Known"}]}],
                "relations": [{"source": "known", "target": "missing"}],
            },
        ),
        (
            "eventmodeling",
            {
                "lanes": [
                    {
                        "id": f"lane_{index}",
                        "frames": [{"id": f"frame_{index}", "label": "Frame"}],
                    }
                    for index in range(129)
                ]
            },
        ),
        (
            "zenuml",
            {
                "participants": ["A", "B"],
                "messages": [{"source": "A", "target": "missing", "label": "Call"}],
            },
        ),
        (
            "zenuml",
            {
                "participants": [f"P{index}" for index in range(500)],
                "messages": [{"source": "P0", "target": "P1", "label": "Call"}],
            },
        ),
    ],
)
def test_eventmodeling_and_zenuml_scene_planning_failures_are_isolated(
    diagram_type: str,
    ir: dict[str, object],
) -> None:
    assert typed_ir_to_scene(diagram_type, ir) is None


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


def test_journey_scene_preserves_sections_scores_actors_and_task_attribution() -> None:
    ir = {
        "title": "Release journey",
        "sections": [
            {
                "label": "Build",
                "bbox": [0, 0, 100, 40],
                "tasks": [
                    {
                        "id": "design",
                        "text": "Design API",
                        "score": 4,
                        "actors": ["Ada", "Bora"],
                        "bbox": [5, 5, 45, 30],
                        "evidence_ids": ["ocr-design"],
                    },
                    {
                        "id": "ship",
                        "label": "Ship",
                        "score": 5,
                        "actors": ["Bora"],
                        "bbox": [55, 5, 95, 30],
                        "evidence_ids": ["ocr-ship"],
                    },
                ],
            }
        ],
    }

    scene = typed_ir_to_scene("journey", ir)

    assert scene is not None
    assert scene.reading_direction == "timeline"
    assert [(element.id, element.text, element.evidence_ids) for element in scene.elements] == [
        ("design", "Design API", ["ocr-design"]),
        ("ship", "Ship", ["ocr-ship"]),
    ]
    assert [group.model_dump() for group in scene.groups] == [
        {
            "id": "journey_section_1",
            "role": "section",
            "label": "Build",
            "bbox": (0.0, 0.0, 100.0, 40.0),
            "member_ids": ["design", "ship"],
        }
    ]
    assert list(typed_ir_semantic_texts("journey", ir, scene)) == [
        "Release journey",
        "Build",
        "Design API",
        "Score 4",
        "Actors Ada, Bora",
        "Ship",
        "Score 5",
        "Actors Bora",
    ]


def test_journey_scene_fails_closed_on_duplicate_task_attribution_ids() -> None:
    assert (
        typed_ir_to_scene(
            "journey",
            {
                "sections": [
                    {
                        "title": "Build",
                        "tasks": [
                            {"id": "same", "label": "First", "score": 1, "actors": ["A"]},
                            {"id": "same", "label": "Second", "score": 2, "actors": ["B"]},
                        ],
                    }
                ]
            },
        )
        is None
    )


def test_kanban_scene_uses_shared_normalized_ids_aliases_and_containment() -> None:
    ir = {
        "columns": [
            {
                "id": "ready lane",
                "title": "Ready",
                "bbox": [0, 0, 40, 80],
                "evidence_ids": ["ocr-ready"],
            }
        ],
        "cards": [
            {
                "id": "task one",
                "text": "Ship",
                "column_id": "ready lane",
                "bbox": [5, 20, 35, 50],
                "evidence_ids": ["ocr-ship"],
            }
        ],
    }

    scene = typed_ir_to_scene("kanban", ir)

    assert scene is not None
    assert scene.reading_direction == "LR"
    assert [(item.id, item.role, item.text, item.evidence_ids) for item in scene.elements] == [
        ("kanban_column_ready_lane", "column", "Ready", ["ocr-ready"]),
        ("kanban_card_task_one", "card", "Ship", ["ocr-ship"]),
    ]
    assert [
        (relation.source_id, relation.target_id, relation.semantic_relation)
        for relation in scene.relations
    ] == [("kanban_column_ready_lane", "kanban_card_task_one", "containment")]
    assert list(typed_ir_semantic_texts("kanban", ir, scene)) == ["Ready", "Ship"]


def test_gitgraph_scene_replays_parent_topology_branch_groups_and_evidence() -> None:
    ir = {
        "initial_branch": "main",
        "direction": "TB",
        "operations": [
            {
                "type": "commit",
                "branch": "main",
                "id": "root",
                "bbox": [0, 0, 10, 10],
                "evidence_ids": ["ocr-root"],
            },
            {
                "type": "branch",
                "name": "feature/api",
                "from": "main",
                "bbox": [15, 0, 25, 10],
                "evidence_ids": ["ocr-feature"],
            },
            {
                "type": "commit",
                "branch": "feature/api",
                "id": "work",
                "tag": "reviewed",
                "bbox": [20, 20, 30, 30],
                "evidence_ids": ["ocr-work"],
            },
            {
                "type": "commit",
                "branch": "main",
                "id": "docs",
                "bbox": [0, 20, 10, 30],
                "evidence_ids": ["ocr-docs"],
            },
            {
                "type": "merge",
                "source": "feature/api",
                "target": "main",
                "id": "merged",
                "bbox": [0, 40, 10, 50],
                "evidence_ids": ["ocr-merged"],
            },
        ],
    }

    scene = typed_ir_to_scene("gitgraph", ir)

    assert scene is not None
    assert scene.reading_direction == "TB"
    assert [(item.id, item.text, item.evidence_ids) for item in scene.elements] == [
        ("git_commit_1", "root", ["ocr-root"]),
        ("git_commit_3", "work", ["ocr-work"]),
        ("git_commit_4", "docs", ["ocr-docs"]),
        ("git_commit_5", "merged", ["ocr-merged"]),
    ]
    assert [
        (relation.id, relation.source_id, relation.target_id, relation.arrow_at_end)
        for relation in scene.relations
    ] == [
        ("git_relation_1", "git_commit_1", "git_commit_3", False),
        ("git_relation_2", "git_commit_1", "git_commit_4", False),
        ("git_relation_3", "git_commit_4", "git_commit_5", False),
        ("git_relation_4", "git_commit_3", "git_commit_5", False),
    ]
    assert [(group.label, group.member_ids) for group in scene.groups] == [
        ("main", ["git_commit_1", "git_commit_4", "git_commit_5"]),
        ("feature/api", ["git_commit_3"]),
    ]
    assert list(typed_ir_semantic_texts("gitgraph", ir, scene)) == [
        "main",
        "feature/api",
        "root",
        "work",
        "reviewed",
        "docs",
        "merged",
    ]


@pytest.mark.parametrize(
    "diagram_type, ir",
    [
        (
            "kanban",
            {
                "columns": [{"id": "ready", "label": "Ready"}],
                "cards": [{"id": "task", "label": "Task", "column_id": "missing"}],
            },
        ),
        (
            "gitgraph",
            {
                "initial_branch": "main",
                "operations": [{"type": "branch", "name": "feature", "from": "main"}],
            },
        ),
    ],
)
def test_planning_scene_adapters_fail_closed_with_the_shared_plan(
    diagram_type: str,
    ir: dict[str, object],
) -> None:
    assert typed_ir_to_scene(diagram_type, ir) is None


def test_packet_scene_preserves_planned_ids_geometry_and_field_provenance() -> None:
    ir = {
        "fields": [
            {
                "id": "source-port",
                "start": 0,
                "end": 15,
                "label": "Source port",
                "bbox": [1, 2, 31, 12],
                "evidence_ids": ["ocr-source", "cell-source"],
            },
            {
                "start": 16,
                "end": 31,
                "label": "Destination port",
                "bbox": [32, 2, 62, 12],
                "evidence_ids": ["ocr-destination"],
            },
        ]
    }

    scene = typed_ir_to_scene("packet", ir)

    assert scene is not None
    assert scene.reading_direction == "LR"
    assert scene.relations == []
    assert [
        (element.id, element.role, element.text, element.bbox, element.evidence_ids)
        for element in scene.elements
    ] == [
        (
            "packet_field_source_port",
            "field",
            "Source port",
            (1.0, 2.0, 31.0, 12.0),
            ["ocr-source", "cell-source"],
        ),
        (
            "packet_field_field_2",
            "field",
            "Destination port",
            (32.0, 2.0, 62.0, 12.0),
            ["ocr-destination"],
        ),
    ]
    assert list(typed_ir_semantic_texts("packet", ir, scene)) == [
        "Source port",
        "Destination port",
    ]


@pytest.mark.parametrize("second_id", ["same-id", "same_id"])
def test_packet_scene_fails_closed_on_duplicate_or_normalized_field_ids(
    second_id: str,
) -> None:
    assert (
        typed_ir_to_scene(
            "packet",
            {
                "fields": [
                    {"id": "same-id", "start": 0, "end": 3, "label": "First"},
                    {"id": second_id, "start": 4, "end": 7, "label": "Second"},
                ]
            },
        )
        is None
    )


def test_railroad_scene_reuses_visible_plan_structure_and_record_provenance() -> None:
    ir = {
        "title": "Hidden grammar title",
        "acc_title": "Hidden accessibility title",
        "rules": [
            {
                "name": "entry-point",
                "comment": "Hidden rule comment",
                "bbox": [1, 2, 30, 12],
                "role": "raw rule role",
                "shape": "diamond",
                "style": "dashed",
                "evidence_ids": ["ocr-entry-rule"],
                "definition": {
                    "type": "sequence",
                    "label": "Hidden sequence label",
                    "bbox": [2, 3, 29, 11],
                    "role": "raw sequence role",
                    "shape": "circle",
                    "style": "thick",
                    "evidence_ids": ["contour-sequence"],
                    "elements": [
                        {
                            "type": "terminal",
                            "value": "begin",
                            "id": "raw-terminal-id",
                            "bbox": [3, 4, 8, 9],
                            "role": "raw terminal role",
                            "shape": "hexagon",
                            "style": "dotted",
                            "evidence_ids": ["ocr-begin"],
                        },
                        {
                            "type": "optional",
                            "evidence_ids": ["contour-optional"],
                            "element": {
                                "type": "special",
                                "text": "whitespace",
                                "evidence_ids": ["ocr-whitespace"],
                            },
                        },
                        {
                            "type": "nonterminal",
                            "name": "identifier",
                            "evidence_ids": ["ocr-identifier-reference"],
                        },
                    ],
                },
            },
            {
                "name": "identifier",
                "evidence_ids": ["ocr-identifier-rule"],
                "definition": {
                    "type": "terminal",
                    "value": "name",
                    "evidence_ids": ["ocr-name"],
                },
            },
        ],
    }

    scene = typed_ir_to_scene("railroad", ir)

    assert scene is not None
    assert scene.diagram_type_candidates == ["railroad"]
    assert scene.reading_direction == "LR"
    assert scene.coordinate_space == "pixels"
    assert scene.groups == []
    assert [element.id for element in scene.elements] == [
        "railroad_rule_entry_point",
        "railroad_rule_identifier",
        "railroad_expression_1",
        "railroad_expression_2",
        "railroad_expression_3",
        "railroad_expression_4",
        "railroad_expression_5",
        "railroad_expression_6",
    ]
    assert [element.role for element in scene.elements] == [
        "rule",
        "rule",
        "sequence",
        "terminal",
        "optional",
        "special",
        "nonterminal",
        "terminal",
    ]
    assert [element.text for element in scene.elements] == [
        "entry-point =",
        "identifier =",
        None,
        "begin",
        None,
        "? whitespace ?",
        "identifier",
        "name",
    ]
    assert [element.shape for element in scene.elements] == [
        None,
        None,
        None,
        "round",
        None,
        "rectangle",
        "rectangle",
        "round",
    ]
    assert all(element.bbox == (0, 0, 0, 0) for element in scene.elements)
    assert [element.evidence_ids for element in scene.elements] == [
        ["ocr-entry-rule"],
        ["ocr-identifier-rule"],
        ["contour-sequence"],
        ["ocr-begin"],
        ["contour-optional"],
        ["ocr-whitespace"],
        ["ocr-identifier-reference"],
        ["ocr-name"],
    ]
    assert [relation.id for relation in scene.relations] == [
        f"railroad_relation_{index}" for index in range(1, 7)
    ]
    assert [(relation.source_id, relation.target_id) for relation in scene.relations] == [
        ("railroad_rule_entry_point", "railroad_expression_1"),
        ("railroad_expression_1", "railroad_expression_2"),
        ("railroad_expression_1", "railroad_expression_3"),
        ("railroad_expression_3", "railroad_expression_4"),
        ("railroad_expression_1", "railroad_expression_5"),
        ("railroad_rule_identifier", "railroad_expression_6"),
    ]
    assert [relation.evidence_ids for relation in scene.relations] == [
        ["contour-sequence"],
        ["ocr-begin"],
        ["contour-optional"],
        ["ocr-whitespace"],
        ["ocr-identifier-reference"],
        ["ocr-name"],
    ]
    assert all(relation.semantic_relation == "containment" for relation in scene.relations)
    assert all(relation.relation_type == "generated_connector" for relation in scene.relations)
    assert all(not relation.arrow_at_start for relation in scene.relations)
    assert all(not relation.arrow_at_end for relation in scene.relations)
    assert all(relation.polyline == [] for relation in scene.relations)
    assert all(
        relation.label is None and relation.line_style is None for relation in scene.relations
    )

    texts = list(typed_ir_semantic_texts("railroad", ir, scene))
    assert texts == [
        "entry-point =",
        "identifier =",
        "begin",
        "? whitespace ?",
        "identifier",
        "name",
    ]
    assert (
        ocr_recall(
            ["entry point identifier begin whitespace name"],
            "",
            generated_texts=texts,
        )
        == 1
    )
    assert (
        ocr_recall(
            [
                "Hidden grammar title Hidden accessibility title Hidden rule comment "
                "Hidden sequence label raw rule role diamond dashed raw-terminal-id"
            ],
            "",
            generated_texts=texts,
        )
        == 0
    )


def test_railroad_scene_and_ocr_use_mapped_native_and_compatible_visible_text() -> None:
    ir = {
        "rules": [
            {
                "name": "style",
                "definition": {
                    "type": "sequence",
                    "elements": [
                        {"type": "terminal", "value": "<script>"},
                        {"type": "terminal", "value": "plain #35; text"},
                        {"type": "terminal", "value": "xstyle:a#foo;tail"},
                        {"type": "terminal", "value": "a＂ ＼ ﹨"},
                        {"type": "nonterminal", "name": "style"},
                    ],
                },
            }
        ]
    }

    scene = typed_ir_to_scene("railroad", ir)

    assert scene is not None
    assert [element.text for element in scene.elements] == [
        "rrmapped_1 =",
        None,
        "〈script〉",
        "plain ＃35; text",
        "xstyle:a＃foo;tail",
        "a″ ∖ ∖",
        "style",
    ]
    assert list(typed_ir_semantic_texts("railroad", ir, scene)) == [
        "rrmapped_1 =",
        "〈script〉",
        "plain ＃35; text",
        "xstyle:a＃foo;tail",
        "a″ ∖ ∖",
        "style",
    ]


@pytest.mark.parametrize(
    "ir",
    [
        {
            "rules": [
                {
                    "name": "entry",
                    "definition": {"type": "nonterminal", "name": "missing"},
                }
            ]
        },
        {
            "rules": [
                {
                    "name": "entry",
                    "definition": {
                        "type": "sequence",
                        "elements": [
                            {"type": "terminal", "value": f"token-{index}"} for index in range(500)
                        ],
                    },
                }
            ]
        },
    ],
)
def test_railroad_scene_fails_closed_with_invalid_or_over_budget_plan(
    ir: dict[str, object],
) -> None:
    assert typed_ir_to_scene("railroad", ir) is None


def test_railroad_scene_uses_the_shared_per_record_evidence_reference_cap() -> None:
    evidence_ids = [f"ocr-{index}" for index in range(MAX_EVIDENCE_REFS)]
    ir = {
        "rules": [
            {
                "name": "entry",
                "definition": {
                    "type": "terminal",
                    "value": "token",
                    "evidence_ids": evidence_ids,
                },
            }
        ]
    }

    scene = typed_ir_to_scene("railroad", ir)

    assert scene is not None
    assert scene.elements[-1].evidence_ids == evidence_ids

    ir["rules"][0]["definition"]["evidence_ids"] = [
        *evidence_ids,
        "ocr-overflow",
    ]
    assert typed_ir_to_scene("railroad", ir) is None


@pytest.mark.parametrize(
    "malformed_evidence_ids",
    ["ocr-token", {"ocr-token": 1}, 7, [7], [{"ocr-token": 1}]],
)
@pytest.mark.parametrize("record_kind", ["rule", "expression"])
def test_railroad_scene_fails_closed_on_non_list_evidence_ids(
    malformed_evidence_ids: object,
    record_kind: str,
) -> None:
    rule = {
        "name": "entry",
        "definition": {"type": "terminal", "value": "token"},
    }
    if record_kind == "rule":
        rule["evidence_ids"] = malformed_evidence_ids
    else:
        rule["definition"]["evidence_ids"] = malformed_evidence_ids

    assert typed_ir_to_scene("railroad", {"rules": [rule]}) is None


@pytest.mark.parametrize(
    ("diagram_type", "ir", "expected_ids"),
    [
        (
            "treeview",
            {
                "root": {
                    "id": "root",
                    "label": "Root",
                    "evidence_ids": ["ocr-root"],
                    "children": [
                        {"label": "First", "evidence_ids": ["ocr-first"]},
                        {"id": "root_1", "label": "Second"},
                    ],
                }
            },
            ["treeview_node_root", "treeview_node_node_2", "treeview_node_root_1"],
        ),
        (
            "ishikawa",
            {
                "effect": {
                    "id": "effect",
                    "label": "Effect",
                    "evidence_ids": ["ocr-effect"],
                },
                "categories": [
                    {"label": "First", "evidence_ids": ["ocr-first"]},
                    {"id": "effect_1", "label": "Second"},
                ],
            },
            ["ishikawa_node_effect", "ishikawa_node_node_2", "ishikawa_node_effect_1"],
        ),
    ],
)
def test_special_hierarchy_scene_reuses_serializer_ids_for_missing_id_collisions(
    diagram_type: str,
    ir: dict[str, object],
    expected_ids: list[str],
) -> None:
    scene = typed_ir_to_scene(diagram_type, ir)

    assert scene is not None
    assert [element.id for element in scene.elements] == expected_ids
    assert [element.text for element in scene.elements] == [
        "Root" if diagram_type == "treeview" else "Effect",
        "First",
        "Second",
    ]
    assert [element.evidence_ids for element in scene.elements] == [
        ["ocr-root"] if diagram_type == "treeview" else ["ocr-effect"],
        ["ocr-first"],
        [],
    ]
    assert [
        (relation.source_id, relation.target_id, relation.semantic_relation)
        for relation in scene.relations
    ] == [
        (expected_ids[0], expected_ids[1], "containment"),
        (expected_ids[0], expected_ids[2], "containment"),
    ]


@pytest.mark.parametrize(
    ("diagram_type", "ir", "expected_texts"),
    [
        (
            "treeview",
            {"root": {"name": "Workspace", "children": [{"name": "Package"}]}},
            ["Workspace", "Package"],
        ),
        (
            "organization",
            {
                "root": {
                    "id": "leadership",
                    "name": "Leadership",
                    "children": [{"id": "engineering", "name": "Engineering"}],
                }
            },
            ["Leadership", "Engineering"],
        ),
        (
            "ishikawa",
            {"effect": {"name": "Delay"}, "categories": [{"name": "People"}]},
            ["Delay", "People"],
        ),
    ],
)
def test_special_hierarchy_scene_preserves_serializer_name_label_aliases(
    diagram_type: str,
    ir: dict[str, object],
    expected_texts: list[str],
) -> None:
    scene = typed_ir_to_scene(diagram_type, ir)

    assert scene is not None
    assert [element.text for element in scene.elements] == expected_texts


@pytest.mark.parametrize(
    ("diagram_type", "ir"),
    [
        (
            "treeview",
            {
                "root": {
                    "id": "root",
                    "label": "Root",
                    "children": [
                        {"id": "same-id", "label": "First"},
                        {"id": "same_id", "label": "Second"},
                    ],
                }
            },
        ),
        (
            "organization",
            {
                "root": {
                    "id": "root",
                    "label": "Root",
                    "children": [
                        {"id": "same-id", "label": "First"},
                        {"id": "same_id", "label": "Second"},
                    ],
                }
            },
        ),
        (
            "treeview",
            {
                "root": {
                    "id": "root",
                    "label": "Root",
                    "children": [
                        {"id": "same", "label": "First"},
                        {"id": "same", "label": "Second"},
                    ],
                }
            },
        ),
        (
            "ishikawa",
            {
                "effect": {"id": "effect", "label": "Effect"},
                "categories": [
                    {"id": "same", "label": "First"},
                    {"id": "same", "label": "Second"},
                ],
            },
        ),
        (
            "ishikawa",
            {
                "effect": {"id": "effect", "label": "Effect"},
                "categories": [
                    {"id": "same-id", "label": "First"},
                    {"id": "same_id", "label": "Second"},
                ],
            },
        ),
    ],
)
def test_special_hierarchy_scene_fails_closed_on_ambiguous_planned_ids(
    diagram_type: str,
    ir: dict[str, object],
) -> None:
    assert typed_ir_to_scene(diagram_type, ir) is None


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
            "datasets": [{"id": "raw", "label": "Raw", "evidence_ids": ["ocr-raw"]}],
            "processes": [{"id": "etl", "label": "ETL", "evidence_ids": ["ocr-etl"]}],
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

    assert organization is not None
    assert [element.id for element in organization.elements] == [
        "treeview_node_ceo",
        "treeview_node_cto",
    ]
    assert organization.relations[0].source_id == "treeview_node_ceo"
    assert organization.relations[0].target_id == "treeview_node_cto"
    assert lineage is not None and lineage.relations[0].semantic_relation == "data_flow"
    assert wardley is not None and wardley.relations[0].evidence_ids == ["line-api"]
    assert venn is not None and len(venn.elements) == 3 and len(venn.relations) == 2


def test_organization_scene_uses_only_visible_fallback_hierarchy_semantics() -> None:
    ir = {
        "direction": "RL",
        "title": "Hidden accessibility title",
        "root": {
            "id": "ceo-primary",
            "label": 'CEO "HQ"\\Ops &copy;',
            "text": "Hidden root text",
            "bbox": [10, 20, 30, 40],
            "role": "raw executive role",
            "shape": "diamond",
            "style": "dashed",
            "evidence_ids": ["ocr-ceo"],
            "children": [
                {
                    "id": "cto",
                    "label": "CTO",
                    "bbox": [50, 20, 70, 40],
                    "role": "raw report role",
                    "shape": "circle",
                    "evidence_ids": ["ocr-cto"],
                }
            ],
        },
    }

    scene = typed_ir_to_scene(
        "organization",
        ir,
        emitted_diagram_type="flowchart-v2",
    )

    assert scene is not None
    assert [(element.id, element.text) for element in scene.elements] == [
        ("treeview_node_ceo_primary", "CEO ″HQ″∖Ops ＆copy;"),
        ("treeview_node_cto", "CTO"),
    ]
    assert [element.role for element in scene.elements] == ["node", "node"]
    assert [element.bbox for element in scene.elements] == [
        (0.0, 0.0, 0.0, 0.0),
        (0.0, 0.0, 0.0, 0.0),
    ]
    assert [element.shape for element in scene.elements] == ["rectangle", "rectangle"]
    assert [element.evidence_ids for element in scene.elements] == [
        ["ocr-ceo"],
        ["ocr-cto"],
    ]
    assert scene.groups == []
    assert len(scene.relations) == 1
    relation = scene.relations[0]
    assert relation.id == "organization_relation_1"
    assert relation.source_id == "treeview_node_ceo_primary"
    assert relation.target_id == "treeview_node_cto"
    assert relation.label is None
    assert relation.relation_type == "generated_connector"
    assert relation.semantic_relation == "containment"
    assert not relation.arrow_at_start and relation.arrow_at_end
    assert relation.polyline == []
    assert relation.line_style is None
    assert relation.evidence_ids == ["ocr-cto"]
    assert scene.reading_direction == "LR"
    assert scene.diagram_type_candidates == ["organization"]
    assert list(typed_ir_semantic_texts("organization", ir, scene)) == [
        "CEO ″HQ″∖Ops ＆copy;",
        "CTO",
    ]


def test_organization_scene_tracks_terminal_treeview_connector_glyphs() -> None:
    ir = {
        "root": {
            "id": "ceo",
            "label": "CEO",
            "children": [{"id": "cto", "label": "CTO"}],
        }
    }

    native = typed_ir_to_scene(
        "organization",
        ir,
        emitted_diagram_type="treeview",
    )
    fallback = typed_ir_to_scene(
        "organization",
        ir,
        emitted_diagram_type="flowchart-v2",
    )

    assert native is not None and fallback is not None
    assert [element.shape for element in native.elements] == [None, None]
    assert not native.relations[0].arrow_at_start
    assert not native.relations[0].arrow_at_end
    assert [element.shape for element in fallback.elements] == ["rectangle", "rectangle"]
    assert not fallback.relations[0].arrow_at_start
    assert fallback.relations[0].arrow_at_end


def test_data_lineage_scene_matches_exact_portable_flowchart_projection() -> None:
    ir = {
        "direction": "RL",
        "description": "Hidden accessibility description",
        "datasets": [
            {
                "id": "raw-data",
                "label": 'Raw "zone"\\set &copy;',
                "text": "Hidden dataset text",
                "bbox": [5, 6, 20, 22],
                "role": "raw source role",
                "shape": "diamond",
                "style": "dashed",
                "evidence_ids": ["ocr-raw"],
            }
        ],
        "processes": [
            {
                "id": "clean-etl",
                "label": "ETL",
                "bbox": [25, 6, 40, 22],
                "role": "raw process role",
                "shape": "circle",
                "evidence_ids": ["ocr-etl"],
            }
        ],
        "relations": [
            {
                "id": "raw-relation-id",
                "source": "raw-data",
                "target": "clean-etl",
                "label": "writes | now;",
                "text": "Hidden relation text",
                "bbox": [20, 10, 25, 12],
                "style": "dashed",
                "bidirectional": True,
                "arrow_at_start": True,
                "arrow_at_end": False,
                "semantic_relation": "causal",
                "evidence_ids": ["line-write"],
            }
        ],
    }

    scene = typed_ir_to_scene("data_lineage", ir)

    assert scene is not None
    assert [(element.id, element.text) for element in scene.elements] == [
        ("data_lineage_dataset_raw_data", "Raw ″zone″∖set ＆copy;"),
        ("data_lineage_process_clean_etl", "ETL"),
    ]
    assert [element.role for element in scene.elements] == ["dataset", "process"]
    assert [element.shape for element in scene.elements] == ["cylinder", "rectangle"]
    assert [element.bbox for element in scene.elements] == [
        (0.0, 0.0, 0.0, 0.0),
        (0.0, 0.0, 0.0, 0.0),
    ]
    assert [element.evidence_ids for element in scene.elements] == [
        ["ocr-raw"],
        ["ocr-etl"],
    ]
    assert scene.groups == []
    assert len(scene.relations) == 1
    relation = scene.relations[0]
    assert relation.id == "data_lineage_relation_1"
    assert relation.source_id == "data_lineage_dataset_raw_data"
    assert relation.target_id == "data_lineage_process_clean_etl"
    assert relation.label == "writes ∣ now⁏"
    assert relation.relation_type == "generated_connector"
    assert relation.semantic_relation == "data_flow"
    assert not relation.arrow_at_start and relation.arrow_at_end
    assert relation.polyline == []
    assert relation.line_style is None
    assert relation.evidence_ids == ["line-write"]
    assert scene.reading_direction == "RL"
    assert scene.diagram_type_candidates == ["data_lineage"]
    texts = list(typed_ir_semantic_texts("data_lineage", ir, scene))
    assert texts == ["Raw ″zone″∖set ＆copy;", "ETL", "writes ∣ now⁏"]
    assert (
        ocr_recall(
            ["Raw zone set copy ETL writes now"],
            "",
            generated_texts=texts,
        )
        == 1
    )
    assert (
        ocr_recall(
            [
                "Hidden accessibility description dataset relation concealed-relation-id "
                "dashed diamond causal"
            ],
            "",
            generated_texts=texts,
        )
        == 0
    )


@pytest.mark.parametrize(
    ("diagram_type", "ir"),
    [
        (
            "organization",
            {
                "root": {
                    "id": "root",
                    "label": "Root",
                    "children": [
                        {"id": f"child-{index}", "label": "Child"} for index in range(500)
                    ],
                }
            },
        ),
        (
            "data_lineage",
            {
                "datasets": [{"id": f"dataset-{index}"} for index in range(250)],
                "processes": [{"id": f"process-{index}"} for index in range(250)],
                "relations": [{"source": "dataset-0", "target": "process-0"}],
            },
        ),
        (
            "data_lineage",
            {
                "datasets": [{"id": "raw"}],
                "processes": [{"id": "etl"}],
                "relations": [{"source": "raw", "target": "missing"}],
            },
        ),
        (
            "organization",
            {
                "root": {
                    "id": "root",
                    "label": "R" * 500,
                    "children": [
                        {
                            "id": f"verbose-child-{index}",
                            "label": "C" * 500,
                        }
                        for index in range(199)
                    ],
                }
            },
        ),
        (
            "data_lineage",
            {
                "datasets": [
                    {
                        "id": f"verbose-dataset-{index}",
                        "label": "D" * 500,
                    }
                    for index in range(200)
                ],
                "processes": [{"id": "etl", "label": "ETL"}],
                "relations": [
                    {
                        "source": "verbose-dataset-0",
                        "target": "etl",
                    }
                ],
            },
        ),
    ],
)
def test_organization_and_data_lineage_scenes_fail_closed_on_invalid_or_over_budget_plans(
    diagram_type: str,
    ir: dict[str, object],
) -> None:
    assert typed_ir_to_scene(diagram_type, ir) is None


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
        ("portfolio", (0, 0, 0, 0), ["contour-portfolio"]),
        ("product", (0, 0, 0, 0), ["ocr-product", "contour-product"]),
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


def test_treemap_scene_reserves_ids_when_source_attribution_ids_are_duplicated() -> None:
    scene = typed_ir_to_scene(
        "treemap",
        {
            "root": {
                "id": "same",
                "label": "Root",
                "children": [{"id": "same", "label": "Leaf", "value": 1}],
            }
        },
    )

    assert scene is not None
    assert [element.id for element in scene.elements] == ["treemap_node_1", "treemap_node_2"]


def test_venn_scene_fails_closed_on_duplicate_attribution_ids() -> None:
    assert (
        typed_ir_to_scene(
            "venn",
            {
                "sets": [{"id": "A", "label": "A"}, {"id": "B", "label": "B"}],
                "intersections": [{"id": "A", "sets": ["A", "B"], "label": "Both"}],
            },
        )
        is None
    )


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
