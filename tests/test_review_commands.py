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


def evidence_scene_ir(evidence_id: str = "ocr:api/18") -> dict:
    ir = scene_ir()
    for element in ir["elements"]:
        element["evidence_ids"] = [evidence_id] if element["id"] == "API" else []
    return ir


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


def test_structured_evidence_relabel_preserves_observation_and_literal_text() -> None:
    for kind in ("ocr_token", "vector_text"):
        evidence_id = f"{kind}:api/18"
        provenance = [
            {
                "id": evidence_id,
                "kind": kind,
                "bbox": [20, 0, 30, 10],
                "text": '  승인입니다. "확정" \\A  ',
                "font_weight": None,
                "score": 0.94,
                "source_block_ids": ["block-1"],
            }
        ]

        result = apply_review_operation(
            {
                "operation": "relabel_node_from_evidence",
                "node_id": "API",
                "evidence_id": evidence_id,
            },
            ir=evidence_scene_ir(evidence_id),
            mermaid_code=flowchart(),
            provenance=provenance,
        )

        assert result.applied
        assert result.ir["elements"][1]["text"] == '승인입니다. "확정" \\A'
        assert result.ir["elements"][1]["evidence_ids"] == [evidence_id]
        assert 'API["승인입니다. \\"확정\\" \\\\A"]' in result.mermaid_code
        assert result.provenance == provenance
        assert not result.provenance_changed
        assert result.history_entry.operation == "relabel_node_from_evidence"
        assert result.history_entry.target == "API"
        assert result.history_entry.before == {"text": "API"}
        assert result.history_entry.after == {
            "text": '승인입니다. "확정" \\A',
            "evidence_id": evidence_id,
        }
        assert result.history_entry.reason == f"selected {kind} evidence {evidence_id}"


def test_structured_evidence_relabel_rejects_untrusted_or_ambiguous_input_atomically() -> None:
    evidence_id = "ocr:api/18"
    valid_evidence = {
        "id": evidence_id,
        "kind": "ocr_token",
        "bbox": [20, 0, 30, 10],
        "text": "결제 승인",
        "score": 0.9,
        "source_block_ids": [],
    }
    cases = [
        (
            {
                "operation": "relabel_node_from_evidence",
                "node_id": "API",
                "evidence_id": evidence_id,
                "label": "client supplied",
            },
            evidence_scene_ir(evidence_id),
            [valid_evidence],
            "invalid_operation",
        ),
        (
            {
                "operation": "relabel_node_from_evidence",
                "node_id": "API",
                "evidence_id": evidence_id,
            },
            scene_ir(),
            [valid_evidence],
            "unresolved_reference",
        ),
        (
            {
                "operation": "relabel_node_from_evidence",
                "node_id": "API",
                "evidence_id": evidence_id,
            },
            evidence_scene_ir(evidence_id),
            [],
            "unresolved_reference",
        ),
        (
            {
                "operation": "relabel_node_from_evidence",
                "node_id": "API",
                "evidence_id": evidence_id,
            },
            evidence_scene_ir(evidence_id),
            [{**valid_evidence, "kind": "vlm_observation"}],
            "invalid_evidence",
        ),
    ]
    shared = evidence_scene_ir(evidence_id)
    shared["elements"][0]["evidence_ids"] = [evidence_id]
    cases.append(
        (
            {
                "operation": "relabel_node_from_evidence",
                "node_id": "API",
                "evidence_id": evidence_id,
            },
            shared,
            [valid_evidence],
            "ambiguous_reference",
        )
    )
    duplicated_link = evidence_scene_ir(evidence_id)
    duplicated_link["elements"][1]["evidence_ids"].append(evidence_id)
    cases.append(
        (
            {
                "operation": "relabel_node_from_evidence",
                "node_id": "API",
                "evidence_id": evidence_id,
            },
            duplicated_link,
            [valid_evidence],
            "ambiguous_reference",
        )
    )

    for operation, ir, provenance, error_code in cases:
        original_ir = deepcopy(ir)
        original_code = flowchart()
        result = apply_review_operation(
            operation,
            ir=ir,
            mermaid_code=original_code,
            provenance=provenance,
        )
        assert not result.applied
        assert result.error_code == error_code
        assert result.ir == original_ir
        assert result.mermaid_code == original_code
        assert ir == original_ir


def test_structured_evidence_relabel_rejects_unsafe_text_noop_and_ambiguous_code() -> None:
    evidence_id = "ocr:api/18"
    for text in (
        None,
        "   ",
        "x" * 201,
        "line\nbreak",
        "line\u2028break",
        "zero\u200bwidth",
        "lone\ud800surrogate",
    ):
        original_ir = evidence_scene_ir(evidence_id)
        result = apply_review_operation(
            {
                "operation": "relabel_node_from_evidence",
                "node_id": "API",
                "evidence_id": evidence_id,
            },
            ir=original_ir,
            mermaid_code=flowchart(),
            provenance=[
                {
                    "id": evidence_id,
                    "kind": "ocr_token",
                    "text": text,
                    "source_block_ids": [],
                }
            ],
        )
        assert not result.applied
        assert result.error_code == "invalid_label"
        assert result.ir == original_ir
        assert result.mermaid_code == flowchart()

    no_change = apply_review_operation(
        {
            "operation": "relabel_node_from_evidence",
            "node_id": "API",
            "evidence_id": evidence_id,
        },
        ir=evidence_scene_ir(evidence_id),
        mermaid_code=flowchart(),
        provenance=[
            {
                "id": evidence_id,
                "kind": "vector_text",
                "text": "API",
                "source_block_ids": [],
            }
        ],
    )
    duplicate_code = apply_review_operation(
        {
            "operation": "relabel_node_from_evidence",
            "node_id": "API",
            "evidence_id": evidence_id,
        },
        ir=evidence_scene_ir(evidence_id),
        mermaid_code=flowchart() + '    API["duplicate"]\n',
        provenance=[
            {
                "id": evidence_id,
                "kind": "vector_text",
                "text": "결제 승인",
                "source_block_ids": [],
            }
        ],
    )

    assert not no_change.applied
    assert no_change.error_code == "no_change"
    assert not duplicate_code.applied
    assert duplicate_code.error_code == "ambiguous_reference"


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


def test_structured_add_and_delete_edge_synchronize_code_ir_and_user_evidence() -> None:
    provenance = [{"id": "ocr-a", "kind": "ocr_token", "bbox": [0, 0, 1, 1]}]
    added = apply_review_operation(
        {"operation": "add_edge", "source_id": "API", "target_id": "DB"},
        ir=scene_ir(),
        mermaid_code=flowchart(),
        provenance=provenance,
        user_relation_id="user-edge-r000001",
        user_evidence_id="user-edit-r000001-edge",
        source_block_ids=["block-1"],
        reason="confirmed connector on source",
    )

    assert added.applied
    relation = added.ir["relations"][-1]
    assert relation["id"] == "user-edge-r000001"
    assert relation["source_id"] == "API" and relation["target_id"] == "DB"
    assert relation["evidence_ids"] == ["user-edit-r000001-edge"]
    assert "    API --> DB\n" in added.mermaid_code
    assert added.provenance_changed
    assert added.provenance[-1]["kind"] == "user_edit"
    assert added.provenance[-1]["text"] == "confirmed connector on source"
    assert added.history_entry.operation == "add_edge"
    assert added.history_entry.target == "user-edge-r000001"

    deleted = apply_review_operation(
        {"operation": "delete_edge", "edge_id": "user-edge-r000001"},
        ir=added.ir,
        mermaid_code=added.mermaid_code,
        provenance=added.provenance,
        reason="connector removed after review",
    )

    assert deleted.applied
    assert [item["id"] for item in deleted.ir["relations"]] == ["E7", "E8"]
    assert "    API --> DB\n" not in deleted.mermaid_code
    assert not deleted.provenance_changed
    assert deleted.provenance == added.provenance
    assert deleted.history_entry.operation == "delete_edge"
    assert deleted.history_entry.before["id"] == "user-edge-r000001"
    assert deleted.history_entry.after == {"deleted": "user-edge-r000001"}


def test_structured_edge_add_delete_reject_ambiguous_or_unanchored_mapping() -> None:
    add_kwargs = {
        "ir": scene_ir(),
        "mermaid_code": flowchart(),
        "user_relation_id": "user-edge-r000001",
        "user_evidence_id": "user-edit-r000001-edge",
        "reason": "source reviewed",
    }
    duplicate = apply_review_operation(
        {"operation": "add_edge", "source_id": "DB", "target_id": "API"},
        **add_kwargs,
    )
    self_loop = apply_review_operation(
        {"operation": "add_edge", "source_id": "API", "target_id": "API"},
        **add_kwargs,
    )
    implicit = apply_review_operation(
        {"operation": "add_edge", "source_id": "API", "target_id": "DB"},
        **{**add_kwargs, "mermaid_code": "flowchart LR\n    API --> DB\n"},
    )
    missing_reason = apply_review_operation(
        {"operation": "add_edge", "source_id": "API", "target_id": "DB"},
        ir=scene_ir(),
        mermaid_code=flowchart(),
        user_relation_id="user-edge-r000001",
        user_evidence_id="user-edit-r000001-edge",
    )
    duplicate_delete = apply_review_operation(
        {"operation": "delete_edge", "edge_id": "E7"},
        ir=scene_ir(),
        mermaid_code=flowchart() + "    DB --> API\n",
    )
    styled_delete = apply_review_operation(
        {"operation": "delete_edge", "edge_id": "E7"},
        ir=scene_ir(),
        mermaid_code=flowchart() + "    linkStyle 0 stroke:#333\n",
    )
    inline_styled_delete = apply_review_operation(
        {"operation": "delete_edge", "edge_id": "E7"},
        ir=scene_ir(),
        mermaid_code=flowchart() + "    classDef x fill:#fff; linkStyle 0 stroke:#333\n",
    )
    unsupported_edge = apply_review_operation(
        {"operation": "add_edge", "source_id": "API", "target_id": "DB"},
        **{**add_kwargs, "mermaid_code": flowchart().replace("DB --> API", "DB --o API")},
    )
    empty_edge_ir = {
        "elements": [
            {"id": "A", "bbox": [0, 0, 10, 10]},
            {"id": "B", "bbox": [20, 0, 30, 10]},
        ],
        "relations": [],
        "groups": [],
        "canvas_size": [100, 100],
    }
    inline_subgraph_edges = [
        'flowchart LR\n  A["A"]\n  B["B"]\n  subgraph G; A --> B; end\n',
        'flowchart LR\n  A["A"]\n  B["B"]\n  subgraph G["G"]; A --> B\n',
        'flowchart LR\n  A["A"]\n  B["B"]\n  subgraph G["G"]; A --> B["inline"]\n',
    ]
    hidden_inline_edges = [
        apply_review_operation(
            {"operation": "add_edge", "source_id": "A", "target_id": "B"},
            ir=empty_edge_ir,
            mermaid_code=code,
            user_relation_id="user-edge-r000001",
            user_evidence_id="user-edit-r000001-edge",
            reason="source reviewed",
        )
        for code in inline_subgraph_edges
    ]
    oversized_reason = apply_review_operation(
        {"operation": "add_edge", "source_id": "API", "target_id": "DB"},
        **{**add_kwargs, "reason": "x" * 4097},
    )
    evidence_collision = apply_review_operation(
        {"operation": "add_edge", "source_id": "API", "target_id": "DB"},
        **{
            **add_kwargs,
            "provenance": [{"id": "user-edit-r000001-edge", "kind": "user_edit"}],
        },
    )
    relation_id_collision = apply_review_operation(
        {"operation": "add_edge", "source_id": "API", "target_id": "DB"},
        **{**add_kwargs, "user_relation_id": "E7"},
    )
    duplicate_declaration = apply_review_operation(
        {"operation": "add_edge", "source_id": "API", "target_id": "DB"},
        **{**add_kwargs, "mermaid_code": flowchart() + '    API["duplicate"]\n'},
    )
    invalid_reason_type = apply_review_operation(
        {"operation": "add_edge", "source_id": "API", "target_id": "DB"},
        **{**add_kwargs, "reason": 1},
    )
    unknown_delete = apply_review_operation(
        {"operation": "delete_edge", "edge_id": "Missing"},
        ir=scene_ir(),
        mermaid_code=flowchart(),
    )
    parallel_ir = scene_ir()
    parallel_ir["relations"].append(
        {"id": "E9", "source_id": "DB", "target_id": "API"}
    )
    parallel_delete = apply_review_operation(
        {"operation": "delete_edge", "edge_id": "E7"},
        ir=parallel_ir,
        mermaid_code=flowchart() + "    DB --> API\n",
    )

    assert not duplicate.applied and duplicate.error_code == "ambiguous_reference"
    assert not self_loop.applied and self_loop.error_code == "invalid_edge"
    assert not implicit.applied and implicit.error_code == "unsupported_artifact"
    assert not missing_reason.applied and missing_reason.error_code == "missing_reason"
    assert not duplicate_delete.applied and duplicate_delete.error_code == "unsupported_artifact"
    assert not styled_delete.applied and styled_delete.error_code == "unsupported_mermaid"
    assert not inline_styled_delete.applied
    assert inline_styled_delete.error_code == "unsupported_mermaid"
    assert not unsupported_edge.applied and unsupported_edge.error_code == "unsupported_mermaid"
    assert all(not result.applied for result in hidden_inline_edges)
    assert all(result.error_code == "unsupported_mermaid" for result in hidden_inline_edges)
    assert [result.mermaid_code for result in hidden_inline_edges] == inline_subgraph_edges
    assert not oversized_reason.applied and oversized_reason.error_code == "missing_reason"
    assert not evidence_collision.applied
    assert evidence_collision.error_code == "ambiguous_reference"
    assert not relation_id_collision.applied
    assert relation_id_collision.error_code == "ambiguous_reference"
    assert not duplicate_declaration.applied
    assert duplicate_declaration.error_code == "unresolved_reference"
    assert not invalid_reason_type.applied and invalid_reason_type.error_code == "missing_reason"
    assert not unknown_delete.applied and unknown_delete.error_code == "unresolved_reference"
    assert not parallel_delete.applied and parallel_delete.error_code == "ambiguous_reference"


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


def test_structured_delete_group_removes_only_exact_group_block() -> None:
    first = apply_review_operation(
        {
            "operation": "group_nodes",
            "node_ids": ["User", "API"],
            "label": "Services",
        },
        ir=scene_ir(),
        mermaid_code=flowchart(),
    )
    deleted = apply_review_operation(
        {"operation": "delete_group", "group_id": "group_User_API"},
        ir=first.ir,
        mermaid_code=first.mermaid_code,
        provenance=[{"id": "ocr-a", "kind": "ocr_token"}],
        reason="group boundary removed",
    )

    assert first.applied and deleted.applied
    assert deleted.ir["groups"] == []
    assert deleted.ir["elements"] == scene_ir()["elements"]
    assert deleted.ir["relations"] == scene_ir()["relations"]
    assert deleted.mermaid_code == flowchart()
    assert [item["id"] for item in deleted.provenance] == ["ocr-a"]
    assert not deleted.provenance_changed
    assert deleted.history_entry.operation == "delete_group"
    assert deleted.history_entry.target == "group_User_API"
    assert deleted.history_entry.before["member_ids"] == ["User", "API"]
    assert deleted.history_entry.after == {"deleted": "group_User_API"}


def test_structured_delete_group_rejects_unknown_mismatch_and_external_reference() -> None:
    grouped = apply_review_operation(
        {
            "operation": "group_nodes",
            "node_ids": ["User", "API"],
            "label": "Services",
        },
        ir=scene_ir(),
        mermaid_code=flowchart(),
    )
    unknown = apply_review_operation(
        {"operation": "delete_group", "group_id": "Missing"},
        ir=grouped.ir,
        mermaid_code=grouped.mermaid_code,
    )
    mismatch = apply_review_operation(
        {"operation": "delete_group", "group_id": "group_User_API"},
        ir=grouped.ir,
        mermaid_code=flowchart(),
    )
    external_reference = apply_review_operation(
        {"operation": "delete_group", "group_id": "group_User_API"},
        ir=grouped.ir,
        mermaid_code=grouped.mermaid_code + "    style group_User_API fill:#fff\n",
    )

    assert not unknown.applied and unknown.error_code == "unresolved_reference"
    assert not mismatch.applied and mismatch.error_code == "unsupported_artifact"
    assert not external_reference.applied
    assert external_reference.error_code == "unsupported_mermaid"


def test_structured_delete_group_uses_exact_crlf_span_with_prefix_ids() -> None:
    ir = {
        "elements": [
            {"id": "A", "bbox": [0, 0, 10, 10]},
            {"id": "B", "bbox": [20, 0, 30, 10]},
            {"id": "C", "bbox": [40, 0, 50, 10]},
            {"id": "D", "bbox": [60, 0, 70, 10]},
        ],
        "relations": [],
        "groups": [
            {
                "id": "group_A",
                "role": "subgraph",
                "label": "Same",
                "bbox": [0, 0, 30, 10],
                "member_ids": ["A", "B"],
            },
            {
                "id": "group_A_B",
                "role": "subgraph",
                "label": "Same",
                "bbox": [40, 0, 70, 10],
                "member_ids": ["C", "D"],
            },
        ],
        "canvas_size": [100, 100],
    }
    code = (
        'flowchart LR\r\n  A["A"]\r\n  B["B"]\r\n  C["C"]\r\n  D["D"]\r\n'
        '  subgraph group_A["Same"]\r\n    A\r\n    B\r\n  end\r\n'
        '  subgraph group_A_B["Same"]\r\n    C\r\n    D\r\n  end\r\n'
    )
    result = apply_review_operation(
        {"operation": "delete_group", "group_id": "group_A"},
        ir=ir,
        mermaid_code=code,
    )

    assert result.applied
    assert [group["id"] for group in result.ir["groups"]] == ["group_A_B"]
    assert 'subgraph group_A["Same"]' not in result.mermaid_code
    assert 'subgraph group_A_B["Same"]\r\n' in result.mermaid_code
    assert result.mermaid_code.count("\r\n") == 9


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
        {"operation": "add_edge", "source_id": "User", "target_id": "DB", "label": "x"},
        {
            "operation": "add_edge",
            "source_id": "User",
            "target_id": "DB",
            "edge_id": "spoofed",
        },
        {"operation": "delete_edge", "edge_id": "E7", "unexpected": True},
        {"operation": "delete_group", "group_id": "group_User_API", "members": ["User"]},
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
