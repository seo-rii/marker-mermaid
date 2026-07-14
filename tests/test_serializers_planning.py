from __future__ import annotations

from xml.etree import ElementTree

import pytest

from marker_mermaid.config import SecurityProfile
from marker_mermaid.models import MAX_ID_CHARS, MAX_SCENE_GROUPS
from marker_mermaid.security import MermaidSecurityScanner
from marker_mermaid.serializers import SerializationError
from marker_mermaid.serializers_planning import (
    MAX_FLOWCHART_FALLBACK_LINES,
    MAX_PLANNING_OUTPUT_CHARS,
    MAX_PLANNING_RECORDS,
    plan_gitgraph_records,
    plan_kanban_records,
    serialize_gitgraph,
    serialize_journey,
    serialize_kanban,
    serialize_planning,
)
from marker_mermaid.validation import CandidateValidator, NodeMermaidRuntime

CASES = {
    "journey": {
        "title": 'Release "journey"',
        "description": "Work from design to launch.",
        "sections": [
            {
                "title": "Build: safely",
                "tasks": [
                    {"label": "Design API", "score": 4, "actors": ["Ada", "보라"]},
                    {"label": "Ship", "score": 5, "actors": ["보라"]},
                ],
            }
        ],
    },
    "kanban": {
        "title": "Release board",
        "description": "Cards move from ready to done.",
        "columns": [
            {"id": "ready", "label": "Ready"},
            {"id": "done", "label": "Done"},
        ],
        "cards": [
            {"id": "write_docs", "label": 'Write [safe] "docs"', "column_id": "ready"},
            {"id": "publish", "label": "Publish", "column_id": "done"},
        ],
    },
    "gitgraph": {
        "title": "Release history",
        "description": "A feature branch is merged into main.",
        "initial_branch": "main",
        "direction": "LR",
        "operations": [
            {"type": "commit", "branch": "main", "id": "root"},
            {"type": "branch", "name": "feature/api", "from": "main", "order": 1},
            {
                "type": "commit",
                "branch": "feature/api",
                "id": 'work "safe"',
                "tag": "reviewed",
                "commit_type": "highlight",
            },
            {"type": "commit", "branch": "main", "id": "docs"},
            {
                "type": "merge",
                "source": "feature/api",
                "target": "main",
                "id": "merge-feature",
            },
        ],
    },
}


@pytest.mark.parametrize(
    ("diagram_type", "prefix", "emitted_type"),
    [
        ("journey", "timeline", "timeline"),
        ("kanban", "kanban", "kanban"),
        ("gitgraph", "gitGraph LR:", "gitgraph"),
    ],
)
def test_planning_serializers_return_explicit_experimental_results(
    diagram_type: str, prefix: str, emitted_type: str
) -> None:
    result = serialize_planning(diagram_type, CASES[diagram_type], experimental=True)

    assert result.code.startswith(prefix)
    assert result.requested_type == diagram_type
    assert result.emitted_type == emitted_type
    assert result.fallback_chain == (
        (diagram_type, emitted_type) if diagram_type == "journey" else (diagram_type,)
    )
    assert result.used_fallback == (diagram_type == "journey")
    assert result.stability == "experimental"
    if diagram_type == "journey":
        assert "forbidden foreignObject" in result.warnings[0]
        assert "scoring layout is not preserved" in result.warnings[1]
    elif diagram_type == "kanban":
        assert result.warnings[0] == (
            "This planning diagram uses an experimental Mermaid reconstruction and requires review."
        )
        assert any("glyphs" in warning for warning in result.warnings[1:])
    else:
        assert result.warnings == (
            "This planning diagram uses an experimental Mermaid reconstruction "
            "and requires review.",
        )
    if diagram_type == "gitgraph":
        assert "experimental and requires review" in result.code
    else:
        assert "accTitle:" not in result.code
        assert "accDescr:" not in result.code
    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(result.code).safe


def test_outputs_are_deterministic_and_use_canonical_ids() -> None:
    for diagram_type, ir in CASES.items():
        assert (
            serialize_planning(diagram_type, ir).code == serialize_planning(diagram_type, ir).code
        )

    journey = serialize_planning("journey", CASES["journey"]).code
    kanban = serialize_planning("kanban", CASES["kanban"]).code
    gitgraph = serialize_planning("gitgraph", CASES["gitgraph"]).code
    assert "Build∶ safely" in journey
    assert "kanban_card_write_docs" in kanban
    assert "&#34;" not in kanban
    assert 'commit id: "work \\"safe\\""' in gitgraph
    assert "&#34;" not in gitgraph
    assert "branch feature_api order: 1" in gitgraph


def test_kanban_plan_preserves_exact_source_ids_aliases_and_resolved_references() -> None:
    column = {"id": "ready lane", "title": "Ready & / : % @"}
    card = {
        "id": "write docs",
        "text": "Write & / : % @",
        "column_id": "ready lane",
    }

    plan = plan_kanban_records({"columns": [column], "cards": [card]})

    assert len(plan.columns) == len(plan.cards) == 1
    planned_column = plan.columns[0]
    planned_card = plan.cards[0]
    assert planned_column.source_record is column
    assert planned_column.source_id == "ready lane"
    assert planned_column.emitted_id == "kanban_column_ready_lane"
    assert planned_column.label == "Ready & / : % @"
    assert planned_column.member_source_ids == ("write docs",)
    assert planned_column.member_emitted_ids == ("kanban_card_write_docs",)
    assert planned_card.source_record is card
    assert planned_card.source_id == "write docs"
    assert planned_card.emitted_id == "kanban_card_write_docs"
    assert planned_card.label == "Write & / : % @"
    assert planned_card.column_source_id == "ready lane"
    assert planned_card.column_emitted_id == "kanban_column_ready_lane"


def test_gitgraph_plan_preserves_exact_branches_aliases_and_parent_topology() -> None:
    operations = [
        {"type": "commit", "branch": "main", "id": "root"},
        {"type": "branch", "id": "feature/api", "from": "main", "order": 2},
        {
            "type": "commit",
            "branch": "feature/api",
            "id": "feature work",
            "tag": "reviewed",
            "style": "highlight",
        },
        {"type": "commit", "branch": "main", "id": "docs"},
        {
            "type": "merge",
            "source": "feature/api",
            "target": "main",
            "id": "merge feature",
        },
    ]

    plan = plan_gitgraph_records(
        {
            "initial_branch": "main",
            "direction": "bt",
            "operations": operations,
        }
    )

    assert plan.initial_branch_source_id == "main"
    assert plan.initial_branch_emitted_id == "main"
    assert plan.direction == "BT"
    assert tuple(operation.source_record for operation in plan.operations) == tuple(operations)
    assert [operation.element_id for operation in plan.commits] == [
        "git_commit_1",
        "git_commit_3",
        "git_commit_4",
        "git_commit_5",
    ]
    assert plan.commits[1].semantic_id == "feature work"
    assert plan.commits[1].owning_branch_source_id == "feature/api"
    assert plan.commits[1].owning_branch_emitted_id == "feature_api"
    assert plan.commits[1].parent_element_ids == ("git_commit_1",)
    assert plan.commits[1].tag == "reviewed"
    assert plan.commits[1].commit_type == "HIGHLIGHT"
    assert plan.commits[-1].source_branch_source_id == "feature/api"
    assert plan.commits[-1].source_branch_emitted_id == "feature_api"
    assert plan.commits[-1].parent_element_ids == ("git_commit_4", "git_commit_3")
    branches = {branch.source_id: branch for branch in plan.branches}
    assert branches["main"].member_element_ids == (
        "git_commit_1",
        "git_commit_4",
        "git_commit_5",
    )
    assert branches["feature/api"].source_record is operations[1]
    assert branches["feature/api"].emitted_id == "feature_api"
    assert branches["feature/api"].parent_source_id == "main"
    assert branches["feature/api"].order == 2
    assert branches["feature/api"].member_element_ids == ("git_commit_3",)


@pytest.mark.parametrize("score", [None, 0, 6, 4.5, "4", True])
def test_journey_never_invents_or_coerces_scores(score: object) -> None:
    with pytest.raises(SerializationError, match="explicit integer from 1 to 5"):
        serialize_planning(
            "journey",
            {
                "sections": [
                    {
                        "title": "Build",
                        "tasks": [{"label": "Test", "score": score, "actors": ["Ada"]}],
                    }
                ]
            },
        )


def test_journey_requires_actor_evidence_and_rejects_duplicates() -> None:
    base = {"sections": [{"title": "Build", "tasks": []}]}
    base["sections"][0]["tasks"] = [{"label": "Test", "score": 3}]
    with pytest.raises(SerializationError, match="actors requires"):
        serialize_planning("journey", base)

    base["sections"][0]["tasks"][0]["actors"] = ["Ada", "Ada"]
    with pytest.raises(SerializationError, match="actors must be unique"):
        serialize_planning("journey", base)


@pytest.mark.parametrize(
    "section, task",
    [
        (
            {"title": "Build", "label": "Release"},
            {"label": "Test", "score": 3, "actors": ["Ada"]},
        ),
        (
            {"title": "Build"},
            {"label": "Test", "text": "Ship", "score": 3, "actors": ["Ada"]},
        ),
    ],
)
def test_journey_rejects_conflicting_compatibility_aliases(section: dict, task: dict) -> None:
    section = {**section, "tasks": [task]}
    with pytest.raises(SerializationError, match="aliases .* must agree"):
        serialize_journey({"sections": [section]})


def test_journey_accepts_identical_compatibility_aliases() -> None:
    result = serialize_journey(
        {
            "sections": [
                {
                    "title": "Build",
                    "label": "Build",
                    "tasks": [
                        {
                            "label": "Test",
                            "text": "Test",
                            "score": 3,
                            "actors": ["Ada"],
                        }
                    ],
                }
            ]
        }
    )
    assert "section Build" in result.code


def test_journey_record_ceiling_is_independent_from_the_smaller_source_budget() -> None:
    boundary = [
        {"label": f"T{index}", "score": 3, "actors": ["Ada"]}
        for index in range(MAX_PLANNING_RECORDS - 1)
    ]
    with pytest.raises(SerializationError, match="source-character limit"):
        serialize_planning("journey", {"sections": [{"title": "Build", "tasks": boundary}]})

    with pytest.raises(SerializationError, match="records exceed deterministic limit"):
        serialize_planning(
            "journey",
            {
                "sections": [
                    {
                        "title": "Build",
                        "tasks": [
                            *boundary,
                            {"label": "Overflow", "score": 3, "actors": ["Ada"]},
                        ],
                    }
                ]
            },
        )


def test_kanban_requires_explicit_ids_and_resolved_column_references() -> None:
    with pytest.raises(SerializationError, match="explicit non-empty ID"):
        serialize_planning("kanban", {"columns": [{"label": "Ready"}], "cards": []})
    with pytest.raises(SerializationError, match="unknown column"):
        serialize_planning(
            "kanban",
            {
                "columns": [{"id": "ready", "label": "Ready"}],
                "cards": [{"id": "task", "label": "Task", "column_id": "missing"}],
            },
        )
    with pytest.raises(SerializationError, match="unknown column"):
        serialize_planning(
            "kanban",
            {
                "columns": [{"id": "ready lane", "label": "Ready"}],
                "cards": [{"id": "task", "label": "Task", "column_id": "ready_lane"}],
            },
        )


@pytest.mark.parametrize(
    "columns, cards, message",
    [
        (
            [{"id": "x" * (MAX_ID_CHARS + 1), "label": "Too long"}],
            [],
            "source identifier limit",
        ),
        (
            [
                {
                    "id": "x" * (MAX_ID_CHARS - len("kanban_column_") + 1),
                    "label": "Too long after prefix",
                }
            ],
            [],
            "emitted identifier limit",
        ),
        (
            [{"id": "ready", "label": "Ready"}],
            [
                {
                    "id": "x" * (MAX_ID_CHARS - len("kanban_card_") + 1),
                    "label": "Too long after prefix",
                    "column_id": "ready",
                }
            ],
            "emitted identifier limit",
        ),
    ],
)
def test_kanban_rejects_source_or_namespaced_identifier_overflow(
    columns: list[dict], cards: list[dict], message: str
) -> None:
    with pytest.raises(SerializationError, match=message):
        plan_kanban_records({"columns": columns, "cards": cards})


def test_kanban_record_limit_accepts_boundary_and_rejects_one_more() -> None:
    boundary_cards = [
        {"id": f"card_{index}", "label": "Card", "column_id": "ready"}
        for index in range(MAX_PLANNING_RECORDS - 1)
    ]
    boundary_ir = {
        "columns": [{"id": "ready", "label": "Ready"}],
        "cards": boundary_cards,
    }
    assert len(plan_kanban_records(boundary_ir).cards) == MAX_PLANNING_RECORDS - 1
    with pytest.raises(SerializationError, match="source-character limit"):
        serialize_kanban(boundary_ir)

    with pytest.raises(SerializationError, match="records exceed deterministic limit"):
        plan_kanban_records(
            {
                **boundary_ir,
                "cards": [
                    *boundary_cards,
                    {"id": "overflow", "label": "Overflow", "column_id": "ready"},
                ],
            }
        )


def test_planning_source_character_budget_accepts_exact_boundary() -> None:
    base_ir = {"columns": [{"id": "ready", "label": "A"}], "cards": []}
    base_code = serialize_kanban(base_ir).code
    fixed_chars = len(base_code) - 1
    boundary_label = "A" * (MAX_PLANNING_OUTPUT_CHARS - fixed_chars)

    boundary = serialize_kanban(
        {"columns": [{"id": "ready", "label": boundary_label}], "cards": []}
    )
    assert len(boundary.code) == MAX_PLANNING_OUTPUT_CHARS

    with pytest.raises(SerializationError, match="source-character limit"):
        serialize_kanban(
            {
                "columns": [{"id": "ready", "label": f"{boundary_label}A"}],
                "cards": [],
            }
        )


@pytest.mark.parametrize(
    "columns, cards",
    [
        (
            [{"id": "ready", "label": "Ready", "title": "Backlog"}],
            [],
        ),
        (
            [{"id": "ready", "label": "Ready"}],
            [
                {
                    "id": "task",
                    "label": "Task",
                    "text": "Other",
                    "column_id": "ready",
                }
            ],
        ),
    ],
)
def test_kanban_rejects_conflicting_compatibility_aliases(
    columns: list[dict], cards: list[dict]
) -> None:
    with pytest.raises(SerializationError, match="aliases .* must agree"):
        plan_kanban_records({"columns": columns, "cards": cards})


def test_kanban_accepts_identical_compatibility_aliases() -> None:
    plan = plan_kanban_records(
        {
            "columns": [{"id": "ready", "label": "Ready", "title": "Ready"}],
            "cards": [
                {
                    "id": "task",
                    "label": "Task",
                    "text": "Task",
                    "column_id": "ready",
                }
            ],
        }
    )
    assert plan.columns[0].label == "Ready"
    assert plan.cards[0].label == "Task"


def test_kanban_namespaces_reserved_ids_consistently_across_native_and_fallback() -> None:
    reserved = ("style", "linkStyle", "classDef", "end", "subgraph", "class")
    column_ir = {
        "columns": [{"id": source_id, "label": f"Column {source_id}"} for source_id in reserved],
        "cards": [],
    }
    card_ir = {
        "columns": [{"id": "lane", "label": "Lane"}],
        "cards": [
            {"id": source_id, "label": f"Card {source_id}", "column_id": "lane"}
            for source_id in reserved
        ],
    }

    for ir, prefix in (
        (column_ir, "kanban_column_"),
        (card_ir, "kanban_card_"),
    ):
        plan = plan_kanban_records(ir)
        emitted_ids = (
            [column.emitted_id for column in plan.columns]
            if prefix == "kanban_column_"
            else [card.emitted_id for card in plan.cards]
        )
        assert emitted_ids == [f"{prefix}{source_id}" for source_id in reserved]
        native = serialize_kanban(ir).code
        fallback = serialize_kanban(ir, native_runtime_valid=False).code
        assert all(emitted_id in native and emitted_id in fallback for emitted_id in emitted_ids)


@pytest.mark.parametrize(
    "columns, cards, message",
    [
        (
            [{"id": "same", "label": "A"}, {"id": "same", "label": "B"}],
            [],
            "duplicate kanban column ID",
        ),
        (
            [{"id": "a-b", "label": "A"}, {"id": "a b", "label": "B"}],
            [],
            "collide after Mermaid normalization",
        ),
        (
            [{"id": "ready", "label": "Ready"}],
            [{"id": "ready", "label": "Card", "column_id": "ready"}],
            "duplicate kanban ID",
        ),
    ],
)
def test_kanban_rejects_duplicate_or_ambiguous_ids(
    columns: list[dict], cards: list[dict], message: str
) -> None:
    with pytest.raises(SerializationError, match=message):
        serialize_planning("kanban", {"columns": columns, "cards": cards})


def test_gitgraph_requires_all_commit_and_merge_ids() -> None:
    with pytest.raises(SerializationError, match="operation 1.id requires"):
        serialize_planning(
            "gitgraph",
            {
                "initial_branch": "main",
                "operations": [{"type": "commit", "branch": "main"}],
            },
        )

    operations = [
        {"type": "commit", "branch": "main", "id": "root"},
        {"type": "branch", "name": "feature", "from": "main"},
        {"type": "commit", "branch": "feature", "id": "work"},
        {"type": "commit", "branch": "main", "id": "docs"},
        {"type": "merge", "source": "feature", "target": "main"},
    ]
    with pytest.raises(SerializationError, match="operation 5.id requires"):
        serialize_planning("gitgraph", {"initial_branch": "main", "operations": operations})


@pytest.mark.parametrize(
    "operations, rejected_field",
    [
        (
            [{"type": "commit", "branch": "main", "id": "root", "name": "ignored"}],
            "name",
        ),
        (
            [
                {"type": "commit", "branch": "main", "id": "root"},
                {
                    "type": "branch",
                    "name": "feature",
                    "from": "main",
                    "commit_type": "normal",
                    "style": "highlight",
                },
            ],
            "commit_type",
        ),
        (
            [
                {"type": "commit", "branch": "main", "id": "root"},
                {"type": "branch", "name": "feature", "from": "main"},
                {"type": "commit", "branch": "feature", "id": "work"},
                {"type": "commit", "branch": "main", "id": "docs"},
                {
                    "type": "merge",
                    "source": "feature",
                    "target": "main",
                    "id": "merged",
                    "name": "ignored",
                },
            ],
            "name",
        ),
    ],
)
def test_gitgraph_rejects_known_fields_irrelevant_to_operation_type(
    operations: list[dict], rejected_field: str
) -> None:
    with pytest.raises(
        SerializationError,
        match=rf"does not allow known field '{rejected_field}'",
    ):
        plan_gitgraph_records({"initial_branch": "main", "operations": operations})


@pytest.mark.parametrize("field", ["source", "target"])
def test_gitgraph_rejects_unresolved_merge_branches(field: str) -> None:
    merge = {"type": "merge", "source": "feature", "target": "main", "id": "merged"}
    merge[field] = "missing"
    with pytest.raises(SerializationError, match=f"unknown {field} branch"):
        serialize_planning(
            "gitgraph",
            {
                "initial_branch": "main",
                "operations": [
                    {"type": "commit", "branch": "main", "id": "root"},
                    {"type": "branch", "name": "feature", "from": "main"},
                    {"type": "commit", "branch": "feature", "id": "work"},
                    {"type": "commit", "branch": "main", "id": "docs"},
                    merge,
                ],
            },
        )


def test_gitgraph_rejects_duplicate_commit_ids_and_implicit_initial_branch() -> None:
    with pytest.raises(SerializationError, match="initial_branch"):
        serialize_planning(
            "gitgraph",
            {"operations": [{"type": "commit", "branch": "main", "id": "root"}]},
        )
    with pytest.raises(SerializationError, match="duplicate gitgraph commit ID"):
        serialize_planning(
            "gitgraph",
            {
                "initial_branch": "main",
                "operations": [
                    {"type": "commit", "branch": "main", "id": "same"},
                    {"type": "commit", "branch": "main", "id": "same"},
                ],
            },
        )


@pytest.mark.parametrize("initial_branch", [" main", "main ", "m-a-i-n", "main!"])
def test_gitgraph_requires_exact_initial_source_branch(initial_branch: str) -> None:
    with pytest.raises(SerializationError, match="exact initial_branch 'main'"):
        plan_gitgraph_records(
            {
                "initial_branch": initial_branch,
                "operations": [{"type": "commit", "branch": initial_branch, "id": "root"}],
            }
        )


def test_gitgraph_operation_limit_accepts_boundary_and_rejects_one_more() -> None:
    boundary = [
        {"type": "commit", "branch": "main", "id": f"commit-{index}"}
        for index in range(MAX_PLANNING_RECORDS)
    ]
    assert (
        len(plan_gitgraph_records({"initial_branch": "main", "operations": boundary}).operations)
        == MAX_PLANNING_RECORDS
    )
    with pytest.raises(SerializationError, match="source-character limit"):
        serialize_gitgraph({"initial_branch": "main", "operations": boundary})

    with pytest.raises(SerializationError, match="operations exceed deterministic limit"):
        plan_gitgraph_records(
            {
                "initial_branch": "main",
                "operations": [
                    *boundary,
                    {"type": "commit", "branch": "main", "id": "overflow"},
                ],
            }
        )


def test_gitgraph_rejects_commit_ids_that_collide_after_grammar_encoding() -> None:
    with pytest.raises(
        SerializationError,
        match="commit IDs collide after Mermaid grammar encoding",
    ):
        plan_gitgraph_records(
            {
                "initial_branch": "main",
                "operations": [
                    {"type": "commit", "branch": "main", "id": "<x>"},
                    {"type": "commit", "branch": "main", "id": "‹x›"},
                ],
            }
        )


def test_gitgraph_branch_count_matches_the_scene_group_limit() -> None:
    boundary_operations = [
        {"type": "commit", "branch": "main", "id": "root"},
        *(
            {"type": "branch", "name": f"branch_{index}", "from": "main"}
            for index in range(MAX_SCENE_GROUPS - 1)
        ),
    ]
    assert (
        len(
            plan_gitgraph_records(
                {"initial_branch": "main", "operations": boundary_operations}
            ).branches
        )
        == MAX_SCENE_GROUPS
    )

    with pytest.raises(SerializationError, match="branch count exceeds Scene group limit"):
        plan_gitgraph_records(
            {
                "initial_branch": "main",
                "operations": [
                    *boundary_operations,
                    {"type": "branch", "name": "overflow", "from": "main"},
                ],
            }
        )


@pytest.mark.parametrize("order", [True, -1, MAX_PLANNING_RECORDS + 1, 1.5, "1"])
def test_gitgraph_branch_order_is_a_bounded_integer(order: object) -> None:
    with pytest.raises(SerializationError, match="order requires an integer from 0"):
        plan_gitgraph_records(
            {
                "initial_branch": "main",
                "operations": [
                    {"type": "commit", "branch": "main", "id": "root"},
                    {"type": "branch", "name": "feature", "from": "main", "order": order},
                ],
            }
        )


def test_gitgraph_rejects_branch_before_first_commit_and_prevents_empty_head_merges() -> None:
    with pytest.raises(SerializationError, match="without a commit"):
        plan_gitgraph_records(
            {
                "initial_branch": "main",
                "operations": [{"type": "branch", "name": "feature", "from": "main"}],
            }
        )


@pytest.mark.parametrize(
    "operation",
    [
        {"type": "commit", "branch": "missing", "id": "work"},
        {"type": "branch", "name": "feature", "from": "missing"},
    ],
)
def test_gitgraph_rejects_unknown_commit_and_branch_references(operation: dict) -> None:
    with pytest.raises(SerializationError, match="unknown"):
        plan_gitgraph_records(
            {
                "initial_branch": "main",
                "operations": [
                    {"type": "commit", "branch": "main", "id": "root"},
                    operation,
                ],
            }
        )


def test_gitgraph_references_source_branch_ids_not_normalized_output_ids() -> None:
    with pytest.raises(SerializationError, match="unknown branch 'feature_api'"):
        plan_gitgraph_records(
            {
                "initial_branch": "main",
                "operations": [
                    {"type": "commit", "branch": "main", "id": "root"},
                    {"type": "branch", "name": "feature/api", "from": "main"},
                    {"type": "commit", "branch": "feature_api", "id": "work"},
                ],
            }
        )


@pytest.mark.parametrize(
    ("second_name", "message"),
    [
        ("feature/api", "duplicate gitgraph branch ID"),
        ("feature api", "collide after Mermaid normalization"),
    ],
)
def test_gitgraph_rejects_raw_and_normalized_branch_collisions(
    second_name: str, message: str
) -> None:
    with pytest.raises(SerializationError, match=message):
        plan_gitgraph_records(
            {
                "initial_branch": "main",
                "operations": [
                    {"type": "commit", "branch": "main", "id": "root"},
                    {"type": "branch", "name": "feature/api", "from": "main"},
                    {"type": "branch", "name": second_name, "from": "main"},
                ],
            }
        )


def test_gitgraph_rejects_self_and_same_head_merges() -> None:
    with pytest.raises(SerializationError, match="into itself"):
        plan_gitgraph_records(
            {
                "initial_branch": "main",
                "operations": [
                    {"type": "commit", "branch": "main", "id": "root"},
                    {"type": "merge", "source": "main", "target": "main", "id": "bad"},
                ],
            }
        )

    with pytest.raises(SerializationError, match="same head"):
        plan_gitgraph_records(
            {
                "initial_branch": "main",
                "operations": [
                    {"type": "commit", "branch": "main", "id": "root"},
                    {"type": "branch", "name": "feature", "from": "main"},
                    {
                        "type": "merge",
                        "source": "feature",
                        "target": "main",
                        "id": "bad",
                    },
                ],
            }
        )


def test_gitgraph_merge_ids_share_the_global_commit_namespace() -> None:
    with pytest.raises(SerializationError, match="duplicate gitgraph commit ID"):
        plan_gitgraph_records(
            {
                "initial_branch": "main",
                "operations": [
                    {"type": "commit", "branch": "main", "id": "root"},
                    {"type": "branch", "name": "feature", "from": "main"},
                    {"type": "commit", "branch": "feature", "id": "work"},
                    {"type": "commit", "branch": "main", "id": "docs"},
                    {
                        "type": "merge",
                        "source": "feature",
                        "target": "main",
                        "id": "docs",
                    },
                ],
            }
        )


@pytest.mark.parametrize("tag", [None, "", "bad\x00tag", "bad\u200btag"])
def test_gitgraph_rejects_empty_or_control_bearing_tags(tag: object) -> None:
    with pytest.raises(SerializationError, match="tag"):
        plan_gitgraph_records(
            {
                "initial_branch": "main",
                "operations": [{"type": "commit", "branch": "main", "id": "root", "tag": tag}],
            }
        )


@pytest.mark.parametrize(
    "metadata",
    [
        {"commit_type": ""},
        {"commit_type": "sparkle"},
        {"commit_type": True},
        {"style": ""},
        {"style": "sparkle"},
    ],
)
def test_gitgraph_rejects_empty_or_invalid_commit_type_aliases(metadata: dict) -> None:
    with pytest.raises(SerializationError, match="commit_type must be"):
        plan_gitgraph_records(
            {
                "initial_branch": "main",
                "operations": [{"type": "commit", "branch": "main", "id": "root", **metadata}],
            }
        )


def test_gitgraph_rejects_conflicting_branch_and_commit_type_aliases() -> None:
    with pytest.raises(SerializationError, match="aliases 'name' and 'id' must agree"):
        plan_gitgraph_records(
            {
                "initial_branch": "main",
                "operations": [
                    {"type": "commit", "branch": "main", "id": "root"},
                    {
                        "type": "branch",
                        "name": "feature",
                        "id": "release",
                        "from": "main",
                    },
                ],
            }
        )

    with pytest.raises(
        SerializationError,
        match="aliases 'commit_type' and 'style' must agree",
    ):
        plan_gitgraph_records(
            {
                "initial_branch": "main",
                "operations": [
                    {
                        "type": "commit",
                        "branch": "main",
                        "id": "root",
                        "commit_type": "normal",
                        "style": "highlight",
                    }
                ],
            }
        )


def test_gitgraph_accepts_identical_case_insensitive_compatibility_aliases() -> None:
    plan = plan_gitgraph_records(
        {
            "initial_branch": "main",
            "operations": [
                {
                    "type": "commit",
                    "branch": "main",
                    "id": "root",
                    "commit_type": "highlight",
                    "style": "HIGHLIGHT",
                },
                {
                    "type": "branch",
                    "name": "feature",
                    "id": "feature",
                    "from": "main",
                },
            ],
        }
    )
    assert plan.commits[0].commit_type == "HIGHLIGHT"
    assert plan.branches[1].source_id == "feature"


@pytest.mark.parametrize(
    ("diagram_type", "serializer"),
    [("kanban", serialize_kanban), ("gitgraph", serialize_gitgraph)],
)
def test_planning_native_runtime_rejection_returns_explicit_flowchart_fallback(
    diagram_type: str, serializer
) -> None:
    result = serializer(CASES[diagram_type], experimental=True, native_runtime_valid=False)

    assert result.requested_type == diagram_type
    assert result.emitted_type == "flowchart"
    assert result.fallback_chain == (diagram_type, "flowchart")
    assert result.used_fallback
    assert result.stability == "experimental"
    assert result.code.startswith("flowchart ")
    assert any("CandidateValidator rejected native" in warning for warning in result.warnings)
    assert any("not preserved" in warning for warning in result.warnings)
    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(result.code).safe


def test_gitgraph_flowchart_fallback_enforces_validator_source_line_budget() -> None:
    operations = [
        {"type": "commit", "branch": "main", "id": "root"},
        {"type": "branch", "name": "feature", "from": "main"},
        {"type": "commit", "branch": "feature", "id": "work"},
        {"type": "commit", "branch": "main", "id": "docs"},
    ]
    operations.extend(
        {
            "type": "merge",
            "source": "feature",
            "target": "main",
            "id": f"merge-{index}",
        }
        for index in range(MAX_PLANNING_RECORDS - len(operations))
    )

    with pytest.raises(
        SerializationError,
        match=f"source-line limit of {MAX_FLOWCHART_FALLBACK_LINES}",
    ):
        serialize_gitgraph(
            {"initial_branch": "main", "operations": operations},
            native_runtime_valid=False,
        )


@pytest.mark.parametrize("native_runtime_valid", [None, 0, 1, "false"])
@pytest.mark.parametrize("serializer", [serialize_kanban, serialize_gitgraph])
def test_planning_runtime_validity_switch_requires_a_boolean(
    serializer, native_runtime_valid: object
) -> None:
    diagram_type = "kanban" if serializer is serialize_kanban else "gitgraph"
    with pytest.raises(SerializationError, match="native_runtime_valid must be a boolean"):
        serializer(CASES[diagram_type], native_runtime_valid=native_runtime_valid)


def test_user_text_cannot_emit_forbidden_mermaid_source() -> None:
    hostile = "https://example.test/ %%{init} <script> call(x) @import"
    ir = {
        "title": hostile,
        "columns": [{"id": "ready", "label": hostile}],
        "cards": [{"id": "task", "label": hostile, "column_id": "ready"}],
    }
    result = serialize_planning("kanban", ir, experimental=True)

    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(result.code).safe
    assert "https:" not in result.code
    assert "%%{" not in result.code
    assert "<script>" not in result.code


def test_unknown_planning_type_is_rejected() -> None:
    with pytest.raises(SerializationError, match="no planning typed serializer"):
        serialize_planning("timeline", {})


@pytest.mark.integration
def test_journey_timeline_svg_preserves_evidence_with_disclosed_compatibility_glyphs() -> None:
    title = 'Journey "quoted" \\ & / : [ ] { } ( ) % @ <title>'
    hostile = "https://evil.invalid/x %%{init: true} call(x) @import &#34; &amp;"
    section = f'Section "quoted" \\ & / : [ ] {{ }} ( ) % @ <section> {hostile}'
    task = f'Task "quoted" \\ & / : [ ] {{ }} ( ) % @ <task> {hostile}'
    actor = f'Actor "quoted" \\ & / : [ ] {{ }} ( ) % @ <actor> {hostile}'
    result = serialize_planning(
        "journey",
        {
            "title": title,
            "sections": [
                {
                    "title": section,
                    "tasks": [{"label": task, "score": 4, "actors": [actor]}],
                }
            ],
        },
    )
    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)

    try:
        outcome = validator.validate(result.code, 20)
    finally:
        runtime.close()

    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(result.code).safe
    assert outcome.runtime.syntax_valid, (result.code, outcome.runtime.error)
    assert outcome.runtime.render_valid, (result.code, outcome.runtime.error, outcome.warnings)
    visible_text = " ".join(
        " ".join(ElementTree.fromstring(outcome.runtime.svg or "").itertext())
        .replace("\u200b", "")
        .split()
    )
    assert title.replace("<", "‹").replace(">", "›") in visible_text
    assert (
        section.replace(":", "∶").replace("&#34;", "＆＃34;").replace("&amp;", "＆amp;")
        in visible_text
    )
    assert (
        task.replace(":", "∶").replace("&#34;", "＆＃34;").replace("&amp;", "＆amp;")
        in visible_text
    )
    assert "Score 4" in visible_text
    actor_text = actor.replace(":", "∶").replace("&#34;", "＆＃34;").replace("&amp;", "＆amp;")
    assert f"Actors {actor_text}" in visible_text
    assert any("∶" in warning for warning in result.warnings)
    assert any("‹" in warning and "›" in warning for warning in result.warnings)
    assert any("＆" in warning and "＃" in warning for warning in result.warnings)


@pytest.mark.integration
def test_planning_serializers_parse_and_render_with_mermaid_11_16() -> None:
    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)
    runtime_types = {"journey": "timeline", "kanban": "kanban", "gitgraph": "gitGraph"}
    try:
        for diagram_type, ir in CASES.items():
            result = serialize_planning(diagram_type, ir, experimental=True)
            outcome = validator.validate(result.code, 20)
            assert outcome.runtime.syntax_valid, (
                diagram_type,
                result.code,
                outcome.runtime.error,
            )
            assert outcome.runtime.render_valid, (
                diagram_type,
                result.code,
                outcome.runtime.error,
                outcome.warnings,
            )
            assert outcome.runtime.diagram_type == runtime_types[diagram_type]
        for diagram_type, serializer in (
            ("kanban", serialize_kanban),
            ("gitgraph", serialize_gitgraph),
        ):
            result = serializer(CASES[diagram_type], experimental=True, native_runtime_valid=False)
            outcome = validator.validate(result.code, 20)
            assert outcome.runtime.syntax_valid, (
                diagram_type,
                result.code,
                outcome.runtime.error,
            )
            assert outcome.runtime.render_valid, (
                diagram_type,
                result.code,
                outcome.runtime.error,
                outcome.warnings,
            )
            assert outcome.runtime.diagram_type == "flowchart-v2"
    finally:
        runtime.close()


@pytest.mark.integration
def test_gitgraph_exact_svg_text_is_visible_and_hostile_tokens_remain_inert() -> None:
    ordinary = 'Proof "quoted" \\ & / : [ ] { } ( ) % @'
    hostile = "https://evil.invalid/x %%{init: true} call(x) @import &#34; <angle>"
    result = serialize_gitgraph(
        {
            "title": ordinary,
            "description": ordinary,
            "initial_branch": "main",
            "direction": "LR",
            "operations": [
                {
                    "type": "commit",
                    "branch": "main",
                    "id": ordinary,
                    "tag": hostile,
                }
            ],
        }
    )
    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)

    try:
        outcome = validator.validate(result.code, 20)
    finally:
        runtime.close()

    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(result.code).safe
    assert outcome.runtime.syntax_valid, (result.code, outcome.runtime.error)
    assert outcome.runtime.render_valid, (result.code, outcome.runtime.error, outcome.warnings)
    root = ElementTree.fromstring(outcome.runtime.svg or "")
    title = root.find("{*}title")
    description = root.find("{*}desc")
    assert title is not None and title.text == ordinary
    assert description is not None and description.text == ordinary
    commit_labels = [
        "".join(element.itertext())
        for element in root.iter()
        if "commit-label" in element.attrib.get("class", "").split()
    ]
    tag_labels = [
        "".join(element.itertext())
        for element in root.iter()
        if "tag-label" in element.attrib.get("class", "").split()
    ]
    assert commit_labels == [ordinary]
    assert [text.replace("\u200b", "") for text in tag_labels] == [
        "https://evil.invalid/x %%{init: true} call(x) @import &#34; ‹angle›"
    ]
    assert any("single-angle glyphs" in warning for warning in result.warnings)


@pytest.mark.integration
def test_kanban_native_and_planning_fallbacks_preserve_supported_visible_text() -> None:
    column_label = 'Ready "quoted" \\ & / : [ ] { } ( ) % @'
    card_label = 'Write "quoted" \\ & / : [ ] { } ( ) % @'
    fallback_column_label = column_label.replace('"', "″").replace("\\", "∖")
    fallback_card_label = card_label.replace('"', "″").replace("\\", "∖")
    kanban_ir = {
        "title": "Release & / : % @",
        "description": "Cards & / : % @",
        "columns": [{"id": "ready lane", "title": column_label}],
        "cards": [{"id": "write docs", "text": card_label, "column_id": "ready lane"}],
    }
    gitgraph_ir = {
        "title": "History & / : % @",
        "description": "Commits & / : % @",
        "initial_branch": "main",
        "operations": [
            {"type": "commit", "branch": "main", "id": card_label, "tag": "Tag & / : % @"}
        ],
    }
    cases = [
        (
            serialize_kanban(kanban_ir),
            (column_label.replace('"', "″"), card_label.replace('"', "″")),
        ),
        (
            serialize_kanban(kanban_ir, native_runtime_valid=False),
            (fallback_column_label, fallback_card_label),
        ),
        (
            serialize_gitgraph(gitgraph_ir, native_runtime_valid=False),
            (fallback_card_label, "Tag & / : % @"),
        ),
    ]
    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)

    try:
        outcomes = [
            (result, validator.validate(result.code, 20), labels) for result, labels in cases
        ]
    finally:
        runtime.close()

    for result, outcome, labels in outcomes:
        assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(result.code).safe
        assert outcome.runtime.syntax_valid, (result.code, outcome.runtime.error)
        assert outcome.runtime.render_valid, (result.code, outcome.runtime.error, outcome.warnings)
        visible_text = " ".join(
            " ".join(ElementTree.fromstring(outcome.runtime.svg or "").itertext())
            .replace("\u200b", "")
            .split()
        )
        for label in labels:
            assert label in visible_text
    assert any("glyphs" in warning for warning in cases[0][0].warnings)
    assert any("glyphs" in warning for warning in cases[1][0].warnings)
    assert any("glyphs" in warning for warning in cases[2][0].warnings)


@pytest.mark.integration
def test_kanban_reserved_id_namespaces_parse_and_render_in_both_grammars() -> None:
    reserved = ("style", "linkStyle", "classDef", "end", "subgraph", "class")
    irs = [
        {
            "columns": [
                {"id": source_id, "label": f"Column {source_id}"} for source_id in reserved
            ],
            "cards": [],
        },
        {
            "columns": [{"id": "lane", "label": "Lane"}],
            "cards": [
                {
                    "id": source_id,
                    "label": f"Card {source_id}",
                    "column_id": "lane",
                }
                for source_id in reserved
            ],
        },
    ]
    results = [
        serialize_kanban(ir, native_runtime_valid=native_runtime_valid)
        for ir in irs
        for native_runtime_valid in (True, False)
    ]
    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)

    try:
        outcomes = [(result, validator.validate(result.code, 20)) for result in results]
    finally:
        runtime.close()

    for result, outcome in outcomes:
        assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(result.code).safe
        assert outcome.runtime.syntax_valid, (result.code, outcome.runtime.error)
        assert outcome.runtime.render_valid, (
            result.code,
            outcome.runtime.error,
            outcome.warnings,
        )
