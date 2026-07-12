from marker_mermaid.candidate_scene import typed_ir_to_scene


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
