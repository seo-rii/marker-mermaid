from copy import deepcopy

from marker_mermaid.review_commands import apply_review_command, parse_review_command


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
