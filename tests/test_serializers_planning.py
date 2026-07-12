from __future__ import annotations

import pytest

from marker_mermaid.config import SecurityProfile
from marker_mermaid.security import MermaidSecurityScanner
from marker_mermaid.serializers import SerializationError
from marker_mermaid.serializers_planning import serialize_planning
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
    else:
        assert result.warnings == (
            "This planning diagram uses an experimental Mermaid reconstruction "
            "and requires review.",
        )
    assert "experimental and requires review" in result.code
    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(result.code).safe


def test_outputs_are_deterministic_and_syntax_text_is_entity_escaped() -> None:
    for diagram_type, ir in CASES.items():
        assert (
            serialize_planning(diagram_type, ir).code == serialize_planning(diagram_type, ir).code
        )

    journey = serialize_planning("journey", CASES["journey"]).code
    kanban = serialize_planning("kanban", CASES["kanban"]).code
    gitgraph = serialize_planning("gitgraph", CASES["gitgraph"]).code
    assert "Build&#58; safely" in journey
    assert "Write &#91;safe&#93; &#34;docs&#34;" in kanban
    assert 'commit id: "work &#34;safe&#34;"' in gitgraph
    assert "branch feature_api order: 1" in gitgraph


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
    finally:
        runtime.close()
