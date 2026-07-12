from copy import deepcopy

from marker_mermaid.review_commands import (
    apply_review_command,
    apply_review_operation,
    parse_review_command,
)


def scene_ir() -> dict:
    return {
        "diagram_type": "flowchart",
        "diagram_type_candidates": ["flowchart"],
        "elements": [
            {"id": "User", "text": "사용자", "bbox": [0, 0, 10, 10]},
            {"id": "API", "text": "API", "bbox": [20, 0, 30, 10]},
            {"id": "DB", "text": "DB", "bbox": [40, 0, 50, 10]},
        ],
        "relations": [
            {"id": "E7", "source_id": "DB", "target_id": "API"},
            {"id": "E8", "source_id": "User", "target_id": "API"},
        ],
        "groups": [],
        "canvas_size": [100, 100],
    }


def flowchart() -> str:
    return (
        "flowchart LR\n"
        '    User["사용자"]\n'
        '    API["API"]\n'
        '    DB["DB"]\n'
        "    DB --> API\n"
        "    User --> API\n"
    )


def test_spec_korean_reverse_edge_updates_ir_and_mermaid_transactionally() -> None:
    result = apply_review_command(
        "DB에서 API로 가는 화살표를 반대로 바꿔 줘.",
        ir=scene_ir(),
        mermaid_code=flowchart(),
    )

    assert result.applied
    assert result.ir["relations"][0]["source_id"] == "API"
    assert result.ir["relations"][0]["target_id"] == "DB"
    assert "    API --> DB\n" in result.mermaid_code
    assert result.history_entry.operation == "reverse_edge"
    assert result.history_entry.target == "E7"
    assert result.history_entry.before == {"source": "DB", "target": "API"}
    assert result.history_entry.after == {"source": "API", "target": "DB"}


def test_explicit_korean_relabel_updates_node_and_quoted_declaration() -> None:
    result = apply_review_command(
        "API 노드의 라벨을 결제 승인으로 바꿔 줘.",
        ir=scene_ir(),
        mermaid_code=flowchart(),
    )

    assert result.applied
    assert result.ir["elements"][1]["text"] == "결제 승인"
    assert 'API["결제 승인"]' in result.mermaid_code
    assert result.history_entry.target == "API"


def test_explicit_korean_group_uses_validated_node_ids() -> None:
    result = apply_review_command(
        "User, API, DB 노드를 하나의 subgraph로 묶어.",
        ir=scene_ir(),
        mermaid_code=flowchart(),
    )

    assert result.applied
    assert result.ir["groups"] == [
        {
            "id": "group_User_API_DB",
            "role": "subgraph",
            "label": None,
            "bbox": [0, 0, 50, 10],
            "member_ids": ["User", "API", "DB"],
        }
    ]
    assert 'subgraph group_User_API_DB["User, API, DB"]' in result.mermaid_code
    assert result.history_entry.operation == "group_nodes"


def test_spec_korean_change_type_marks_code_for_regeneration() -> None:
    result = apply_review_command(
        "이 영역은 일반 flowchart가 아니라 sequence diagram이야.",
        ir=scene_ir(),
        mermaid_code=flowchart(),
    )

    assert result.applied
    assert result.ir["diagram_type"] == "sequence"
    assert result.ir["diagram_type_candidates"] == ["sequence", "flowchart"]
    assert result.mermaid_code == flowchart()
    assert result.regeneration_required
    assert result.history_entry.after == {"diagram_type": "sequence"}


def test_spec_spatial_and_ordinal_examples_are_rejected_as_ambiguous() -> None:
    original = scene_ir()
    for command in (
        "왼쪽의 세 박스를 하나의 subgraph로 묶어.",
        "두 번째 node의 label은 결제 승인이야.",
    ):
        result = apply_review_command(command, ir=original)
        assert not result.applied
        assert result.error_code == "ambiguous_reference"
        assert result.ir == original
        assert result.history_entry is None


def test_english_commands_cover_all_supported_operations() -> None:
    assert parse_review_command("reverse edge DB -> API").operation == "reverse_edge"
    assert parse_review_command("relabel node API to Payment approval").label == "Payment approval"
    assert parse_review_command("group nodes User, API as Services").node_ids == ["User", "API"]
    assert parse_review_command("change diagram type to state").diagram_type == "state"


def test_unresolved_id_preserves_all_original_artifacts() -> None:
    original_ir = scene_ir()
    original_code = flowchart()
    snapshot = deepcopy(original_ir)

    result = apply_review_command(
        "Missing 노드의 라벨을 새 이름으로 바꿔 줘.",
        ir=original_ir,
        mermaid_code=original_code,
    )

    assert not result.applied
    assert result.error_code == "unresolved_reference"
    assert result.ir == snapshot
    assert result.mermaid_code == original_code
    assert original_ir == snapshot


def test_duplicate_edges_are_rejected_as_ambiguous_without_partial_edit() -> None:
    original = scene_ir()
    original["relations"].append({"id": "E9", "source_id": "DB", "target_id": "API"})

    result = apply_review_command("reverse edge DB -> API", ir=original)

    assert not result.applied
    assert result.error_code == "ambiguous_reference"
    assert result.ir == original


def test_code_failure_rolls_back_an_ir_edit() -> None:
    code_without_plain_edge = flowchart().replace("    DB --> API\n", "    DB -->|query| API\n")

    result = apply_review_command(
        "reverse edge DB -> API",
        ir=scene_ir(),
        mermaid_code=code_without_plain_edge,
    )

    assert not result.applied
    assert result.ir == scene_ir()
    assert result.mermaid_code == code_without_plain_edge


def test_type_change_requires_ir_and_unsupported_or_oversized_input_is_safe() -> None:
    no_ir = apply_review_command("change diagram type to sequence", mermaid_code=flowchart())
    unsupported = apply_review_command("delete everything", ir=scene_ir())
    oversized = apply_review_command("x" * 501, ir=scene_ir())

    assert no_ir.error_code == "unsupported_artifact"
    assert unsupported.error_code == "unsupported_command"
    assert oversized.error_code == "input_too_large"


def test_label_is_escaped_without_becoming_mermaid_syntax() -> None:
    result = apply_review_command(
        'relabel node API to Approved "locally"',
        ir=scene_ir(),
        mermaid_code=flowchart(),
    )

    assert result.applied
    assert 'API["Approved \\"locally\\""]' in result.mermaid_code


def test_structured_reconnect_uses_relation_id_and_updates_both_artifacts() -> None:
    result = apply_review_operation(
        {
            "operation": "reconnect_edge",
            "edge_id": "E7",
            "source_id": "User",
            "target_id": "DB",
        },
        ir=scene_ir(),
        mermaid_code=flowchart(),
        reason="source reviewed",
    )

    assert result.applied
    assert result.ir["relations"][0]["source_id"] == "User"
    assert result.ir["relations"][0]["target_id"] == "DB"
    assert "    User --> DB\n" in result.mermaid_code
    assert "    DB --> API\n" not in result.mermaid_code
    assert result.history_entry.operation == "reconnect_edge"
    assert result.history_entry.target == "E7"
    assert result.history_entry.before == {"source": "DB", "target": "API"}
    assert result.history_entry.after == {"source": "User", "target": "DB"}
    assert result.history_entry.reason == "source reviewed"


def test_structured_group_uses_explicit_ids_and_preserves_provenance() -> None:
    provenance = [{"id": "ocr-a", "kind": "ocr_token", "bbox": [0, 0, 1, 1]}]
    result = apply_review_operation(
        {
            "operation": "group_nodes",
            "node_ids": ["API", "User"],
            "label": "Services",
        },
        ir=scene_ir(),
        mermaid_code=flowchart(),
        provenance=provenance,
        reason="confirmed logical boundary",
    )

    assert result.applied
    assert result.ir["groups"] == [
        {
            "id": "group_User_API",
            "role": "subgraph",
            "label": "Services",
            "bbox": [0, 0, 30, 10],
            "member_ids": ["User", "API"],
        }
    ]
    assert 'subgraph group_User_API["Services"]' in result.mermaid_code
    assert "        User\n" in result.mermaid_code
    assert "        API\n" in result.mermaid_code
    assert [item["id"] for item in result.provenance] == ["ocr-a"]
    assert provenance == [{"id": "ocr-a", "kind": "ocr_token", "bbox": [0, 0, 1, 1]}]
    assert not result.provenance_changed
    assert result.history_entry.operation == "group_nodes"
    assert result.history_entry.target == "group_User_API"
    assert result.history_entry.reason == "confirmed logical boundary"
    duplicate_set = apply_review_operation(
        {
            "operation": "group_nodes",
            "node_ids": ["User", "API"],
            "label": "Duplicate",
        },
        ir=result.ir,
        mermaid_code=result.mermaid_code,
    )
    assert not duplicate_set.applied
    assert duplicate_set.error_code == "ambiguous_reference"


def test_structured_group_rejects_ambiguous_membership_and_invalid_schema() -> None:
    grouped_ir = scene_ir()
    grouped_ir["groups"] = [
        {
            "id": "Existing",
            "role": "subgraph",
            "bbox": [0, 0, 10, 10],
            "member_ids": ["User"],
        }
    ]
    cases = (
        (
            {
                "operation": "group_nodes",
                "node_ids": ["User", "API"],
                "label": "Services",
            },
            grouped_ir,
            flowchart(),
            "unsupported_artifact",
        ),
        (
            {
                "operation": "group_nodes",
                "node_ids": ["User", "API"],
                "label": "Services",
            },
            scene_ir(),
            flowchart() + "    subgraph Existing\n        User\n    end\n",
            "unsupported_artifact",
        ),
        (
            {"operation": "group_nodes", "node_ids": ["User"], "label": "Services"},
            scene_ir(),
            flowchart(),
            "invalid_operation",
        ),
        (
            {
                "operation": "group_nodes",
                "node_ids": ["User", "User"],
                "label": "Services",
            },
            scene_ir(),
            flowchart(),
            "invalid_operation",
        ),
        (
            {
                "operation": "group_nodes",
                "node_ids": ["User", "unsafe id"],
                "label": "Services",
            },
            scene_ir(),
            flowchart(),
            "invalid_identifier",
        ),
        (
            {"operation": "group_nodes", "node_ids": ["User", "API"]},
            scene_ir(),
            flowchart(),
            "invalid_operation",
        ),
        (
            {"operation": "group_nodes", "node_ids": ["User", "API"], "label": ""},
            scene_ir(),
            flowchart(),
            "invalid_label",
        ),
        (
            {
                "operation": "group_nodes",
                "node_ids": ["User", "API"],
                "label": "x" * 201,
            },
            scene_ir(),
            flowchart(),
            "invalid_label",
        ),
        (
            {
                "operation": "group_nodes",
                "node_ids": [f"N{index}" for index in range(51)],
                "label": "Oversized",
            },
            scene_ir(),
            flowchart(),
            "invalid_operation",
        ),
    )
    for operation, ir, code, error_code in cases:
        result = apply_review_operation(operation, ir=ir, mermaid_code=code)
        assert not result.applied
        assert result.error_code == error_code
        assert result.ir == ir
        assert result.mermaid_code == code


def test_structured_group_requires_exact_existing_subgraph_mapping_and_node_declarations() -> None:
    grouped_ir = scene_ir()
    grouped_ir["groups"] = [
        {
            "id": "Existing",
            "role": "subgraph",
            "bbox": [0, 0, 10, 10],
            "member_ids": ["User"],
        }
    ]
    matching_code = flowchart() + '    subgraph Existing["Existing"]\n        User\n    end\n'
    added = apply_review_operation(
        {"operation": "group_nodes", "node_ids": ["DB", "API"], "label": "Data"},
        ir=grouped_ir,
        mermaid_code=matching_code,
    )
    mismatched = apply_review_operation(
        {"operation": "group_nodes", "node_ids": ["DB", "API"], "label": "Data"},
        ir=grouped_ir,
        mermaid_code=flowchart(),
    )
    duplicate_declaration = apply_review_operation(
        {"operation": "group_nodes", "node_ids": ["User", "API"], "label": "Services"},
        ir=scene_ir(),
        mermaid_code=flowchart() + '    API["duplicate"]\n',
    )
    implicit_declaration = apply_review_operation(
        {"operation": "group_nodes", "node_ids": ["User", "API"], "label": "Services"},
        ir=scene_ir(),
        mermaid_code="flowchart LR\n    User --> API\n",
    )
    existing_duplicate_declaration = apply_review_operation(
        {"operation": "group_nodes", "node_ids": ["DB", "API"], "label": "Data"},
        ir=grouped_ir,
        mermaid_code=matching_code + '    User["duplicate"]\n',
    )
    natural_existing_duplicate = apply_review_command(
        "group nodes API, DB as Data",
        ir=grouped_ir,
        mermaid_code=matching_code + '    User["duplicate"]\n',
    )
    colliding_group_ir = scene_ir()
    colliding_group_ir["groups"] = [
        {
            "id": "User",
            "role": "subgraph",
            "bbox": [0, 0, 10, 10],
            "member_ids": ["User"],
        }
    ]
    group_node_collision = apply_review_operation(
        {"operation": "group_nodes", "node_ids": ["DB", "API"], "label": "Data"},
        ir=colliding_group_ir,
        mermaid_code=flowchart() + '    subgraph User["Existing"]\n        User\n    end\n',
    )

    assert added.applied
    assert [group["id"] for group in added.ir["groups"]] == ["Existing", "group_API_DB"]
    assert 'subgraph group_API_DB["Data"]' in added.mermaid_code
    assert not mismatched.applied
    assert mismatched.error_code == "unsupported_artifact"
    assert not duplicate_declaration.applied
    assert duplicate_declaration.error_code == "unresolved_reference"
    assert not implicit_declaration.applied
    assert implicit_declaration.error_code == "unresolved_reference"
    assert not existing_duplicate_declaration.applied
    assert existing_duplicate_declaration.error_code == "unsupported_artifact"
    assert not natural_existing_duplicate.applied
    assert natural_existing_duplicate.error_code == "unsupported_artifact"
    assert not group_node_collision.applied
    assert group_node_collision.error_code == "ambiguous_reference"


def test_structured_group_rejects_invalid_or_out_of_canvas_member_bbox() -> None:
    for bbox in (
        [False, 0, 10, 10],
        [0, 0, float("nan"), 10],
        [10, 0, 5, 10],
        [0, 0, 101, 10],
    ):
        ir = scene_ir()
        ir["elements"][0]["bbox"] = bbox
        result = apply_review_operation(
            {
                "operation": "group_nodes",
                "node_ids": ["User", "API"],
                "label": "Services",
            },
            ir=ir,
            mermaid_code=flowchart(),
        )
        assert not result.applied
        assert result.error_code in {"unsupported_ir", "invalid_operation"}


def test_structured_group_hashes_long_canonical_member_ids_deterministically() -> None:
    first_id = "Node" + "A" * 40
    second_id = "Node" + "B" * 40
    ir = {
        "elements": [
            {"id": first_id, "bbox": [0, 0, 10, 10]},
            {"id": second_id, "bbox": [20, 0, 30, 10]},
        ],
        "relations": [],
        "groups": [],
        "canvas_size": [100, 100],
    }
    code = (
        "flowchart LR\n"
        f'    {first_id}["First"]\n'
        f'    {second_id}["Second"]\n'
    )
    forward = apply_review_operation(
        {
            "operation": "group_nodes",
            "node_ids": [first_id, second_id],
            "label": "Long IDs",
        },
        ir=ir,
        mermaid_code=code,
    )
    reversed_input = apply_review_operation(
        {
            "operation": "group_nodes",
            "node_ids": [second_id, first_id],
            "label": "Long IDs",
        },
        ir=ir,
        mermaid_code=code,
    )

    assert forward.applied and reversed_input.applied
    group_id = forward.ir["groups"][0]["id"]
    assert group_id == reversed_input.ir["groups"][0]["id"]
    assert group_id.startswith("group_") and len(group_id) == 26


def test_source_anchored_add_creates_node_code_and_user_evidence_transactionally() -> None:
    result = apply_review_operation(
        {
            "operation": "add_node",
            "node_id": "Review",
            "label": "Manual review",
            "bbox": [60, 20, 90, 40],
        },
        ir=scene_ir(),
        mermaid_code=flowchart(),
        provenance=[],
        user_evidence_id="user-edit-r000001-Review",
        source_block_ids=["block-1"],
        reason="confirmed on source image",
    )

    assert result.applied
    node = result.ir["elements"][-1]
    assert node["id"] == "Review"
    assert node["bbox"] == [60.0, 20.0, 90.0, 40.0]
    assert node["evidence_ids"] == ["user-edit-r000001-Review"]
    assert 'Review["Manual review"]' in result.mermaid_code
    assert result.provenance_changed
    assert result.provenance[-1]["kind"] == "user_edit"
    assert result.provenance[-1]["source_block_ids"] == ["block-1"]
    assert result.history_entry.operation == "add_node"
    assert result.history_entry.after["evidence_ids"] == ["user-edit-r000001-Review"]


def test_source_anchored_add_rejects_missing_reason_and_out_of_canvas_bbox() -> None:
    original_ir = scene_ir()
    original_code = flowchart()
    operation = {
        "operation": "add_node",
        "node_id": "Review",
        "label": "Manual review",
        "bbox": [60, 20, 120, 40],
    }
    missing_reason = apply_review_operation(
        operation,
        ir=original_ir,
        mermaid_code=original_code,
        user_evidence_id="user-edit-r000001-Review",
    )
    invalid_bbox = apply_review_operation(
        operation,
        ir=original_ir,
        mermaid_code=original_code,
        user_evidence_id="user-edit-r000001-Review",
        reason="source checked",
    )
    boolean_bbox = apply_review_operation(
        {**operation, "bbox": [True, 20, 80, 40]},
        ir=original_ir,
        mermaid_code=original_code,
        user_evidence_id="user-edit-r000001-Review",
        reason="source checked",
    )

    assert not missing_reason.applied
    assert missing_reason.error_code == "missing_reason"
    assert not invalid_bbox.applied
    assert invalid_bbox.error_code == "invalid_bbox"
    assert not boolean_bbox.applied
    assert boolean_bbox.error_code == "invalid_operation"
    assert original_ir == scene_ir()
    assert invalid_bbox.ir == scene_ir()
    assert invalid_bbox.mermaid_code == original_code


def test_structured_delete_requires_one_to_one_explicit_flowchart_mapping() -> None:
    result = apply_review_operation(
        {"operation": "delete_node", "node_id": "API"},
        ir=scene_ir(),
        mermaid_code=flowchart(),
    )

    assert result.applied
    assert [item["id"] for item in result.ir["elements"]] == ["User", "DB"]
    assert result.ir["relations"] == []
    assert 'API["API"]' not in result.mermaid_code
    assert "DB --> API" not in result.mermaid_code
    assert "User --> API" not in result.mermaid_code
    assert result.history_entry.operation == "delete_node"
    assert result.history_entry.target == "API"
    assert len(result.history_entry.before["relations"]) == 2


def test_structured_operations_reject_invalid_schema_without_mutation() -> None:
    original_ir = scene_ir()
    original_code = flowchart()
    snapshot = deepcopy(original_ir)

    for operation in (
        {"operation": "reconnect_edge", "edge_id": "E7", "source_id": "User"},
        {"operation": "delete_node", "node_id": "API", "unexpected": True},
        {
            "operation": "group_nodes",
            "node_ids": ["User", "API"],
            "label": "Services",
            "unexpected": True,
        },
        {"operation": "move_node", "node_id": "API"},
        {"operation": "delete_node", "node_id": "unsafe id"},
    ):
        result = apply_review_operation(
            operation,
            ir=original_ir,
            mermaid_code=original_code,
        )
        assert not result.applied
        assert result.error_code in {"invalid_operation", "invalid_identifier"}
        assert result.ir == snapshot
        assert result.mermaid_code == original_code
        assert original_ir == snapshot


def test_structured_delete_rejects_grouped_or_extra_mermaid_references() -> None:
    grouped_ir = scene_ir()
    grouped_ir["groups"] = [
        {
            "id": "Services",
            "role": "subgraph",
            "bbox": [0, 0, 30, 10],
            "member_ids": ["User", "API"],
        }
    ]
    grouped = apply_review_operation(
        {"operation": "delete_node", "node_id": "API"},
        ir=grouped_ir,
        mermaid_code=flowchart(),
    )
    styled = apply_review_operation(
        {"operation": "delete_node", "node_id": "API"},
        ir=scene_ir(),
        mermaid_code=flowchart() + "    style API fill:#fff\n",
    )

    assert not grouped.applied
    assert grouped.error_code == "unsupported_ir"
    assert not styled.applied
    assert styled.error_code == "unsupported_mermaid"


def test_structured_reconnect_rejects_duplicate_or_labeled_edge_mapping() -> None:
    duplicate = apply_review_operation(
        {
            "operation": "reconnect_edge",
            "edge_id": "E7",
            "source_id": "User",
            "target_id": "DB",
        },
        ir=scene_ir(),
        mermaid_code=flowchart() + "    DB --> API\n",
    )
    labeled_code = flowchart().replace("    DB --> API\n", "    DB -->|query| API\n")
    labeled = apply_review_operation(
        {
            "operation": "reconnect_edge",
            "edge_id": "E7",
            "source_id": "User",
            "target_id": "DB",
        },
        ir=scene_ir(),
        mermaid_code=labeled_code,
    )

    assert not duplicate.applied
    assert duplicate.error_code == "ambiguous_reference"
    assert not labeled.applied
    assert labeled.error_code == "unresolved_reference"
