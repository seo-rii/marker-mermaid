from marker_mermaid.candidate_scene import typed_ir_semantic_texts, typed_ir_to_scene
from marker_mermaid.scoring import ocr_recall


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
    assert [item.id for item in mindmap.elements] == ["root", "child"]
    assert mindmap.relations[0].source_id == "root"


def test_unsupported_or_empty_typed_ir_is_unavailable():
    assert typed_ir_to_scene("gantt", {"sections": []}) is None
    assert typed_ir_to_scene("flowchart", {"nodes": []}) is None


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
