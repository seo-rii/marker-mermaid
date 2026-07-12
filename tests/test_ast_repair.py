from __future__ import annotations

from marker_mermaid.ast_repair import DeterministicMermaidRepair, normalize_identifier


def test_repairs_bom_fence_quote_and_terminal_newline_with_events() -> None:
    source = '\ufeff```mermaid\nflowchart LR\n    bad-id["Start]\n```'

    result = DeterministicMermaidRepair().repair(source)

    assert result.source == 'flowchart LR\n    bad_id["Start"]\n'
    assert [event.operation for event in result.events] == [
        "remove_bom",
        "unwrap_markdown_fence",
        "close_unambiguous_label_quote",
        "normalize_identifier",
        "add_terminal_newline",
    ]
    assert result.security_preserved
    assert result.idempotent


def test_quote_repair_refuses_ambiguous_inline_edge_and_non_flowchart() -> None:
    inline = 'flowchart LR\n    A["open] --> B["closed"]'
    extra_bracket = 'flowchart LR\n    A["open]still-open]'
    sequence = 'sequenceDiagram\n    A->>B: "open'

    assert DeterministicMermaidRepair().repair(inline).source == inline + "\n"
    assert DeterministicMermaidRepair().repair(extra_bracket).source == extra_bracket + "\n"
    assert DeterministicMermaidRepair().repair(sequence).source == sequence + "\n"


def test_nested_fence_is_not_unwrapped_by_lexical_guessing() -> None:
    source = "```mermaid\nflowchart LR\n```nested\n```"

    result = DeterministicMermaidRepair().repair(source)

    assert result.source == source + "\n"
    assert [event.operation for event in result.events] == ["add_terminal_newline"]


def test_normalizes_only_explicit_structural_identifier_uses() -> None:
    source = (
        'flowchart LR\n    1-start["Start"]\n    final.node["Final"]\n    1-start --> final.node\n'
    )

    result = DeterministicMermaidRepair().repair(source)

    assert 'n_1_start["Start"]' in result.source
    assert 'final_node["Final"]' in result.source
    assert "n_1_start --> final_node" in result.source
    assert normalize_identifier("1-start") == "n_1_start"


def test_does_not_normalize_identifier_when_it_also_appears_in_label() -> None:
    source = 'flowchart LR\n    bad-id["bad-id"]\n'

    result = DeterministicMermaidRepair().repair(source)

    assert result.source == source
    assert not result.changed
    assert [issue.code for issue in result.issues] == ["nonportable_identifier"]


def test_identifier_collision_is_reported_instead_of_guessed() -> None:
    source = 'flowchart LR\n    bad-id["First"]\n    bad_id["Second"]\n'

    result = DeterministicMermaidRepair().repair(source)

    assert result.source == source
    assert [issue.code for issue in result.issues] == ["nonportable_identifier"]


def test_removes_only_exact_duplicate_and_reports_conflicting_duplicate() -> None:
    source = 'flowchart LR\n    A["One"]\n    A["One"]\n    A["Different"]\n'

    result = DeterministicMermaidRepair().repair(source)

    assert result.source.count('A["One"]') == 1
    assert any(event.operation == "remove_exact_duplicate_node" for event in result.events)
    assert [issue.code for issue in result.issues] == ["conflicting_node_declaration"]


def test_duplicate_declarations_with_distinct_comments_are_not_removed() -> None:
    source = 'flowchart LR\n    A["One"] %% evidence-one\n    A["One"] %% evidence-two\n'

    result = DeterministicMermaidRepair().repair(source)

    assert result.source == source
    assert not result.changed
    assert [issue.code for issue in result.issues] == ["duplicate_node_declaration"]


def test_reports_unresolved_edge_endpoint_without_inventing_node() -> None:
    source = 'flowchart LR\n    A["Known"]\n    A --> Missing\n'

    result = DeterministicMermaidRepair().repair(source)

    issue = next(issue for issue in result.issues if issue.code == "unresolved_edge_endpoint")
    assert issue.identifiers == ("Missing",)
    assert "Missing[" not in result.source


def test_budget_is_bounded_and_reports_non_idempotent_partial_pass() -> None:
    source = '\ufeff```mermaid\nflowchart LR\n    A["One"]\n```'

    result = DeterministicMermaidRepair(event_budget=1).repair(source)

    assert [event.operation for event in result.events] == ["remove_bom"]
    assert result.budget_used == 1
    assert result.budget_exhausted
    assert not result.idempotent
    assert any(issue.code == "repair_budget_exhausted" for issue in result.issues)


def test_security_guard_discards_repairs_that_expose_forbidden_syntax() -> None:
    source = '\ufeff```mermaid\nflowchart LR\nclick A "https://example.test"\n```'

    result = DeterministicMermaidRepair().repair(source)

    assert result.source == source
    assert not result.changed
    assert not result.security_preserved
    assert [issue.code for issue in result.issues] == ["security_guard_rejected"]


class _CanonicalAdapter:
    def parse(self, source: str) -> list[str]:
        return source.splitlines()

    def render(self, ast: list[str]) -> str:
        return "\n".join(line.rstrip() for line in ast) + "\n"


class _UnstableAdapter:
    def parse(self, source: str) -> str:
        return source

    def render(self, ast: str) -> str:
        return ast + "\n"


class _FailingAdapter:
    def parse(self, source: str) -> str:
        raise ValueError("unsupported diagram")

    def render(self, ast: str) -> str:
        return ast


def test_optional_ast_adapter_reports_stable_semantic_round_trip() -> None:
    result = DeterministicMermaidRepair(ast_adapter=_CanonicalAdapter()).repair(
        "flowchart LR\n    A --> B\n"
    )

    assert result.round_trip.attempted
    assert result.round_trip.parsed
    assert result.round_trip.stable


def test_optional_ast_adapter_reports_instability_and_errors_without_mutation() -> None:
    source = "flowchart LR\n    A --> B\n"

    unstable = DeterministicMermaidRepair(ast_adapter=_UnstableAdapter()).repair(source)
    failing = DeterministicMermaidRepair(ast_adapter=_FailingAdapter()).repair(source)

    assert not unstable.round_trip.stable
    assert not failing.round_trip.parsed
    assert failing.round_trip.error == "ValueError: unsupported diagram"
    assert unstable.source == failing.source == source


def test_source_limits_fail_closed_without_scanning_or_editing() -> None:
    source = "flowchart LR\n    A --> B\n"
    result = DeterministicMermaidRepair(max_source_chars=5).repair(source)

    assert result.source == source
    assert result.idempotent
    assert [issue.code for issue in result.issues] == ["source_limit_exceeded"]
