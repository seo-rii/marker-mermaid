from __future__ import annotations

import time
from collections import Counter

import pytest

from marker_mermaid.config import MermaidConfig, PublishPolicy, SecurityProfile
from marker_mermaid.models import MAX_TEXT_CHARS, MermaidCandidate
from marker_mermaid.protocols import RuntimeResult
from marker_mermaid.scoring import (
    aggregate_scores,
    decide_publication,
    numeric_consistency,
    numeric_token_multiset,
    ocr_recall,
    semantic_score,
)
from marker_mermaid.security import MermaidSecurityScanner
from marker_mermaid.validation import CandidateValidator, NodeMermaidRuntime, inspect_svg


@pytest.mark.parametrize(
    "code",
    [
        'flowchart LR\nclick A "https://example.com"',
        "%%{init: {'theme':'dark'}}%%\nflowchart LR\nA-->B",
        'flowchart LR\nA["<script>alert(1)</script>"]',
        "flowchart LR\nA-->B\nclassDef x fill:url(https://example.com/a)",
        'flowchart LR\nA["javascript:alert(1)"]',
        'flowchart LR\nA["<b>unsafe</b>"]',
        'flowchart LR\nA["//example.com/path"]',
        "---\nconfig:\n  theme: dark\n---\nflowchart LR\nA-->B",
    ],
)
def test_strict_scanner_rejects_active_or_external_syntax(code):
    assert not MermaidSecurityScanner(SecurityProfile.STRICT).scan(code).safe


def test_strict_scanner_allows_only_exact_state_pseudostate_declarations() -> None:
    scanner = MermaidSecurityScanner(SecurityProfile.STRICT)
    code = (
        "stateDiagram-v2\n"
        "    state decision <<choice>>\n"
        "    state parallel_start <<fork>>\n"
        "    state parallel_end <<join>>\n"
    )

    assert scanner.scan(code).safe
    assert not scanner.scan("flowchart LR\n    state decision <<choice>>\n").safe
    assert not scanner.scan("stateDiagram-v2\n    state decision <<script>>\n").safe
    assert not scanner.scan("stateDiagram-v2\n    state decision <<choice>> <b>\n").safe


@pytest.mark.parametrize(
    "payload",
    [
        ":" * MAX_TEXT_CHARS,
        "{" * MAX_TEXT_CHARS,
        "<a" * (MAX_TEXT_CHARS // 2),
    ],
    ids=["colon", "brace", "unclosed-html-openers"],
)
def test_statement_scanner_is_bounded_for_long_state_label_punctuation(payload: str) -> None:
    code = (
        "stateDiagram-v2\n"
        "    accTitle: State fixture\n"
        "    accDescr: State fixture description\n"
        f'    state "{payload}" as S\n'
    )
    started = time.perf_counter()

    report = MermaidSecurityScanner(SecurityProfile.STRICT).scan(code)

    assert time.perf_counter() - started < 5
    assert report.safe


def test_style_only_allows_local_style_but_not_remote_css():
    scanner = MermaidSecurityScanner(SecurityProfile.STYLE_ONLY)
    assert scanner.scan("flowchart LR\nA-->B\nstyle A fill:#fff").safe
    assert scanner.scan("flowchart LR\nA-->B; style A fill:#fff").safe
    assert scanner.scan("flowchart LR\nA-->B; classDef local fill:#fff").safe
    assert scanner.scan("flowchart LR\nA-->B; linkStyle 0 stroke:#fff").safe
    assert not scanner.scan("flowchart LR\nA-->B\nstyle A fill:url(http://x)").safe
    assert not scanner.scan('flowchart LR\nA-->B; click A "#local"').safe


@pytest.mark.parametrize(
    "profile",
    [
        SecurityProfile.STYLE_ONLY,
        SecurityProfile.TRUSTED_LOCAL,
        SecurityProfile.SANDBOX_EXPERIMENTAL,
    ],
)
def test_non_strict_profiles_preserve_semicolon_local_style_support(profile):
    scanner = MermaidSecurityScanner(profile)

    assert scanner.scan("flowchart LR\nA-->B; style A fill:#fff").safe


@pytest.mark.parametrize("profile", list(SecurityProfile))
def test_all_profiles_reject_semicolon_click_statements(profile):
    report = MermaidSecurityScanner(profile).scan('flowchart LR\nA-->B; click A "#local"')

    assert not report.safe
    assert [finding.rule for finding in report.findings] == ["click"]


@pytest.mark.parametrize(
    "statement",
    [
        'click A "#local"',
        "style A fill:#fff",
        "classDef local fill:#fff",
        "linkStyle 0 stroke:#fff",
    ],
)
def test_strict_scanner_rejects_forbidden_semicolon_statements(statement):
    report = MermaidSecurityScanner(SecurityProfile.STRICT).scan(
        f"flowchart LR\nA-->B; {statement}"
    )

    assert not report.safe


def test_statement_boundaries_ignore_semicolons_inside_quoted_labels_and_comments():
    scanner = MermaidSecurityScanner(SecurityProfile.STRICT)

    assert scanner.scan('flowchart LR\nA["label; style is text; click is text"] --> B').safe
    assert scanner.scan('flowchart LR\nA --> B["label; click is text"]').safe
    assert scanner.scan("flowchart LR\n  %% ; style is a comment\nA --> B").safe


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_multiline_double_quoted_labels_do_not_create_statement_boundaries(newline):
    code = newline.join(
        [
            "flowchart LR",
            'A["first line',
            "; style is label text",
            '; click is also label text"] --> B',
        ]
    )

    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(code).safe


def test_reference_free_text_scores_do_not_invent_numbers():
    assert ocr_recall(["결제 승인 거절"], 'flowchart LR\nA["결제 승인"]') == pytest.approx(2 / 3)
    assert numeric_consistency(["1.5 20% 99"], "pie\n X : 20%") == pytest.approx(0.5)
    assert numeric_consistency(["20"], "pie\n X : 20\n Y : 999") == pytest.approx(2 / 3)
    assert (
        numeric_consistency(
            ["20"],
            'pie\n    accDescr: 20 was observed\n    "Approved" : 20\n',
        )
        == 1
    )


def test_numeric_consistency_ignores_only_quadrant_grammar_slot_numbers() -> None:
    code = """quadrantChart
    accDescr: Portfolio 99
    quadrant-1 "Expand 2026"
    quadrant-4 "Improve"
    "Project 3": [0.2, 0.4]
"""

    assert numeric_consistency(["2026 Project 3 0.2 0.4"], code) == 1
    assert (
        numeric_consistency(
            ["1 2 3 4"],
            """quadrantChart
    quadrant-1 "One"
    quadrant-2 "Two"
    quadrant-3 "Three"
    quadrant-4 "Four"
""",
        )
        == 0
    )


def test_numeric_consistency_preserves_quadrant_like_numbers_outside_grammar_slots() -> None:
    assert (
        numeric_consistency(
            ["1 20 4 80"],
            'pie\n    "quadrant-1" : 20\n    "quadrant-4" : 80\n',
        )
        == 1
    )
    assert (
        numeric_consistency(
            ["5 0.5 0.5"],
            'quadrantChart\n    quadrant-5 "Outlier"\n    "Point": [0.5, 0.5]\n',
        )
        == 1
    )


def test_numeric_consistency_comments_cannot_copy_source_numbers() -> None:
    code = """pie
    %% hostile direct candidate copied 999 from source
    "Actual 2 %% label text": 20 %% copied 999 again
"""

    assert numeric_consistency(["999"], code) == 0
    assert numeric_consistency(["Actual 2 20"], code) == 1


@pytest.mark.parametrize(
    ("header", "data_line", "source_text"),
    [
        ("pie", '"Slice 2" : 20', "Slice 2 20"),
        (
            "xychart-beta",
            "x-axis [1, 2]\ny-axis 0 --> 20\nline [5, 10]",
            "1 2 0 20 5 10",
        ),
        ("gantt", "Task 2 : task, 2026-01-01, 1d", "Task 2 2026-01-01 1d"),
        ("packet-beta", '0-7: "Field 2"', "0-7 Field 2"),
        ("treemap-beta", '"Region 2": 20', "Region 2 20"),
        ("venn-beta", 'set A["Set 2"]: 20', "Set 2 20"),
    ],
)
def test_numeric_consistency_native_titles_do_not_lower_valid_data_scores(
    header: str,
    data_line: str,
    source_text: str,
) -> None:
    code = f"{header}\n    title Metadata 999\n    {data_line}\n"

    assert numeric_consistency([source_text], code) == 1


def test_numeric_consistency_preserves_sankey_metadata_like_csv_rows() -> None:
    code = """sankey-beta
title 2026,Target,20
title: 2027,Target,21
accTitle: 2028,Target,22
accDescr {A},Target 2029,23
"""

    assert numeric_consistency(["2026 20 2027 21 2028 22 2029 23"], code) == 1


@pytest.mark.parametrize("header", ["flowchart LR", "graph TB"])
def test_numeric_consistency_excludes_accessibility_metadata_in_numeric_fallbacks(
    header: str,
) -> None:
    code = f"""{header}
    accTitle: Fallback 999
    accDescr {{ Hidden 998 }}; A["Observed 2"]
"""

    assert numeric_consistency(["Observed 2"], code) == 1
    assert numeric_consistency(["999 998"], code) == 0


def test_numeric_consistency_preserves_title_text_inside_actual_labels() -> None:
    code = 'pie\n    title Metadata 999\n    "title 2" : 20\n'

    assert numeric_consistency(["title 2 20"], code) == 1
    assert numeric_consistency(["999"], code) == 0


def test_numeric_consistency_excludes_native_title_metadata_after_data_lines() -> None:
    code = """xychart-beta
    x-axis [1, 2]
    y-axis 0 --> 20
    line [5, 10]
    title Late metadata 999
"""

    assert numeric_consistency(["1 2 0 20 5 10"], code) == 1
    assert numeric_consistency(["999"], code) == 0


def test_numeric_consistency_excludes_colon_and_block_accessibility_metadata() -> None:
    code = """quadrantChart
    accTitle: Portfolio 999
    title: Colon metadata 998
    accDescr {
        Source-only values 997 and 996
        remain metadata 995
    }
    title Native metadata 994
    quadrant-1 "Group 4"
    "Point 2": [0.2, 0.4]
"""

    assert numeric_consistency(["Group 4 Point 2 0.2 0.4"], code) == 1
    assert numeric_consistency(["999 998 997 996 995 994"], code) == 0


def test_numeric_consistency_preserves_data_before_inline_accessibility_metadata() -> None:
    xychart = """xychart-beta
    x-axis [1, 2]
    y-axis 0 --> 20
    line [5, 10]; accTitle: Hidden 999
    bar [6, 11]; accDescr { Hidden 998 }; line [7, 12]
    bar [8, 13]; accDescr {
        Hidden 997
    }; line [9, 14]
    bar [10, 15]; title Hidden 996
"""
    quadrant = """quadrantChart
    x-axis "Low" --> "High"
    y-axis "Low" --> "High"
    quadrant-1 "Group 4"
    "Point 2": [0.2, 0.4]; accDescr { Hidden 995 }; "Point 3": [0.6, 0.8]
"""

    assert numeric_consistency(["1 2 0 20 5 10 6 11 7 12 8 13 9 14 10 15"], xychart) == 1
    assert numeric_consistency(["Group 4 Point 2 0.2 0.4 Point 3 0.6 0.8"], quadrant) == 1
    assert numeric_consistency(["999 998 997 996"], xychart) == 0
    assert numeric_consistency(["995"], quadrant) == 0


def test_numeric_consistency_fails_closed_when_metadata_suffix_budget_is_exhausted() -> None:
    metadata_chain = "; ".join(f"accDescr {{ Hidden {value} }}" for value in range(1000, 1033))
    code = f"xychart-beta\nline [1]; {metadata_chain}; line [999]\n"

    # The final hallucinated data statement must not disappear behind bounded scanning.
    assert numeric_consistency(["1"], code) == 0


@pytest.mark.integration
def test_numeric_metadata_capabilities_match_mermaid_11_16_runtime() -> None:
    valid_cases = [
        (
            f'{header}\naccTitle: Hidden 999\naccDescr {{ Hidden 998 }}; A["Observed 2"]\n',
            "flowchart-v2",
        )
        for header in ("flowchart LR", "graph TB")
    ] + [
        (
            "xychart-beta\nline [5]; accDescr { Hidden 997 }; bar [6]\n",
            "xychart",
        ),
        (
            'quadrantChart\n"Point 2": [0.2, 0.4]; '
            'accDescr { Hidden 996 }; "Point 3": [0.6, 0.8]\n',
            "quadrantChart",
        ),
        (
            "sankey-beta\n"
            "title 2026,Target,20\n"
            "title: 2027,Target,21\n"
            "accTitle: 2028,Target,22\n"
            "accDescr {A},Target 2029,23\n",
            "sankey",
        ),
    ]
    invalid_codes = [
        f'{header}\n{title}\nA["Observed 2"]\n'
        for header in ("flowchart LR", "graph TB")
        for title in ("title: Hidden 997", "title Hidden 996")
    ]
    runtime = NodeMermaidRuntime()
    try:
        valid_results = [
            runtime.validate_and_render(code, 20) for code, _expected_type in valid_cases
        ]
        invalid_results = [runtime.validate_and_render(code, 20) for code in invalid_codes]
    finally:
        runtime.close()

    assert all(result.render_valid for result in valid_results)
    assert [result.diagram_type for result in valid_results] == [
        expected_type for _code, expected_type in valid_cases
    ]
    assert all(not result.syntax_valid for result in invalid_results)


def test_numeric_consistency_accepts_detached_source_token_counters() -> None:
    source = numeric_token_multiset(["-2 20% 0.5 0.5"])
    before = source.copy()

    assert source == Counter({"0.5": 2, "-2": 1, "20%": 1})
    assert numeric_consistency(source, "xychart-beta\nline [-2, 20%, 0.5, 0.5]\n") == 1
    assert source == before


def test_ocr_recall_preserves_occurrences_and_normalizes_unicode_labels():
    assert ocr_recall(["X X X"], "flowchart LR", generated_texts=["x"]) == pytest.approx(1 / 3)
    assert ocr_recall(["X X X"], "flowchart LR", generated_texts=["X", "x"]) == pytest.approx(2 / 3)
    assert ocr_recall(["Ａ 승인"], "flowchart LR", generated_texts=["a 승인"]) == 1


def test_ocr_recall_keeps_large_repetition_counts_compact():
    assert ocr_recall(
        Counter({"x": 1_000_000_000}),
        "flowchart LR",
        generated_texts=["x"],
    ) == pytest.approx(1e-9)


def test_direct_ocr_recall_ignores_accessibility_metadata_headers_and_node_ids():
    code = (
        "flowchart LR\n"
        "    accTitle: Payment\n"
        "    accDescr: Payment was observed\n"
        '    Payment["Other"]\n'
    )

    assert ocr_recall(["Payment"], code) == 0
    assert ocr_recall(["Other"], code) == 1


def test_direct_gantt_recall_counts_visible_labels_not_schedule_fields():
    code = (
        "gantt\n"
        "    dateFormat YYYY-MM-DD\n"
        "    section Review phase\n"
        "    Review payment :done, t1, 2026-07-01, 2026-07-02\n"
    )

    assert ocr_recall(["Review phase Review payment"], code) == 1
    assert ocr_recall(["done t1 2026"], code) == 0


def test_aggregate_requires_a_semantic_metric():
    config = MermaidConfig()
    assert aggregate_scores({"syntax": 1, "render": 1}, config) is None
    assert aggregate_scores({"syntax": 1, "render": 1, "type_fitness": 0.5}, config) is not None
    assert semantic_score({"syntax": 1, "render": 1, "type_fitness": 0}, config) == 0


def test_runtime_scores_cannot_dilute_zero_semantic_evidence_into_publication():
    config = MermaidConfig()
    scores = {"syntax": 1.0, "render": 1.0, "type_fitness": 0.0}
    candidate = MermaidCandidate(
        candidate_id="c",
        generation_method="direct_mermaid",
        diagram_type="flowchart",
        syntax_valid=True,
        render_valid=True,
        scores=scores,
        aggregate_score=aggregate_scores(scores, config),
    )

    assert candidate.aggregate_score is not None
    assert candidate.aggregate_score >= config.publish_min_score
    assert not decide_publication(candidate, config).publish


@pytest.mark.parametrize(
    ("policy", "score", "published"),
    [
        (PublishPolicy.BEST_EFFORT_VALIDATED, 0.50, True),
        (PublishPolicy.BEST_EFFORT_VALIDATED, 0.49, False),
        (PublishPolicy.STRICT_VALIDATED, 0.70, True),
        (PublishPolicy.STRICT_VALIDATED, 0.69, False),
        (PublishPolicy.REVIEW_REQUIRED, 0.90, False),
        (PublishPolicy.SIDECAR_ONLY, 0.90, False),
    ],
)
def test_publication_truth_table(policy, score, published):
    candidate = MermaidCandidate(
        candidate_id="c",
        generation_method="typed_ir",
        diagram_type="flowchart",
        syntax_valid=True,
        render_valid=True,
        scores={"syntax": 1, "render": 1, "type_fitness": score},
        aggregate_score=score,
    )
    assert decide_publication(candidate, MermaidConfig(publish_policy=policy)).publish is published


def test_best_effort_never_publishes_grade_d_even_with_zero_threshold() -> None:
    candidate = MermaidCandidate(
        candidate_id="c",
        generation_method="typed_ir",
        diagram_type="flowchart",
        syntax_valid=True,
        render_valid=True,
        scores={"type_fitness": 0.49},
        aggregate_score=0.49,
    )

    decision = decide_publication(
        candidate,
        MermaidConfig(publish_min_score=0.0),
    )

    assert decision.grade == "D"
    assert not decision.publish
    assert decision.review_required


def test_invalid_candidate_is_never_published():
    candidate = MermaidCandidate(
        candidate_id="c",
        generation_method="direct_mermaid",
        diagram_type="flowchart",
        syntax_valid=True,
        render_valid=False,
        aggregate_score=1,
    )
    assert not decide_publication(candidate, MermaidConfig()).publish


def test_svg_inspection_rejects_external_links_and_scripts():
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1">'
        '<a href="https://example.com"><script>x</script></a></svg>'
    )
    findings = inspect_svg(svg, SecurityProfile.STRICT)
    assert "rendered SVG contains an external href" in findings
    assert "rendered SVG contains forbidden <script>" in findings


@pytest.mark.parametrize(
    ("attribute", "value"),
    [("d", "MNaN,NaN Z"), ("points", "0,0 Infinity,1"), ("transform", "translate(NaN 0)")],
)
def test_svg_inspection_rejects_non_finite_geometry(attribute: str, value: str) -> None:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1">'
        f'<path {attribute}="{value}"/></svg>'
    )

    assert f"rendered SVG contains non-finite geometry attribute {attribute}" in inspect_svg(
        svg, SecurityProfile.STRICT
    )


def test_svg_inspection_rejects_zero_area_gantt_tasks() -> None:
    invisible = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50" '
        'aria-roledescription="gantt">'
        '<rect id="task-a" class="task task0" x="10" y="10" width="0" height="20"/>'
        "</svg>"
    )
    milestone = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50" '
        'aria-roledescription="gantt">'
        '<rect id="task-a" class="task milestone task0" x="10" y="10" '
        'width="20" height="20"/>'
        "</svg>"
    )
    unrelated = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50">'
        '<rect class="task" width="0" height="20"/>'
        "</svg>"
    )

    finding = "rendered Gantt contains a non-visible task rectangle"
    assert finding in inspect_svg(invisible, SecurityProfile.STRICT)
    assert finding not in inspect_svg(milestone, SecurityProfile.STRICT)
    assert finding not in inspect_svg(unrelated, SecurityProfile.STRICT)
    assert finding not in inspect_svg(
        invisible,
        SecurityProfile.STRICT,
        diagram_type="journey",
    )


@pytest.mark.parametrize(
    "svg",
    [None, "", "  \n", b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>'],
)
def test_validator_rejects_runtime_render_success_without_non_empty_svg(svg):
    class MissingSvgRuntime:
        def validate_and_render(self, code, timeout_seconds):
            return RuntimeResult(
                syntax_valid=True,
                render_valid=True,
                diagram_type="flowchart-v2",
                svg=svg,
            )

        def close(self):
            pass

    outcome = CandidateValidator(MissingSvgRuntime(), SecurityProfile.STRICT).validate(
        "flowchart LR\nA --> B\n", 1
    )

    assert outcome.runtime.syntax_valid
    assert not outcome.runtime.render_valid
    assert outcome.runtime.svg is None
    assert outcome.runtime.png is None
    assert outcome.runtime.error == (
        "Mermaid runtime reported render success without a non-empty SVG artifact"
    )
    assert outcome.warnings == ["rendered SVG artifact is missing or empty"]


@pytest.mark.parametrize(
    "css",
    [
        "@import url(https://attacker.example/x.css);",
        ".node { fill: url( https://attacker.example/fill.svg ); }",
    ],
)
def test_svg_inspection_rejects_external_css_in_style_text(css):
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1">'
        f'<style>{css}</style><rect width="1" height="1"/></svg>'
    )

    assert "rendered SVG contains external CSS" in inspect_svg(svg, SecurityProfile.STRICT)


def test_security_failure_does_not_call_runtime(fake_runtime):
    outcome = CandidateValidator(fake_runtime, SecurityProfile.STRICT).validate(
        'flowchart LR\nclick A "https://example.com"', 1
    )
    assert not outcome.runtime.syntax_valid
    assert fake_runtime.calls == []


def test_semicolon_security_failure_does_not_call_runtime(fake_runtime):
    outcome = CandidateValidator(fake_runtime, SecurityProfile.STRICT).validate(
        "flowchart LR\nA --> B; style A fill:#fff", 1
    )

    assert not outcome.runtime.syntax_valid
    assert outcome.warnings == ["security:style_syntax:line 2"]
    assert fake_runtime.calls == []


@pytest.mark.parametrize(
    "code",
    [
        'flowchart LR\nA[don\'t]; click A "#local"',
        'flowchart LR\nA[foo`bar]; click A "#local"',
        'flowchart LR\nA["foo\\"]; click A "#local"',
        'flowchart LR\nA[100%%]; click A "#local"',
    ],
)
def test_semicolon_click_bypasses_are_blocked_before_runtime(fake_runtime, code):
    outcome = CandidateValidator(fake_runtime, SecurityProfile.STRICT).validate(code, 1)

    assert not outcome.runtime.syntax_valid
    assert outcome.warnings == ["security:click:line 2"]
    assert fake_runtime.calls == []


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_valid_multiline_quoted_labels_reach_runtime(fake_runtime, newline):
    code = newline.join(
        [
            "flowchart LR",
            'A["first line',
            "; style is label text",
            '; click is also label text"] --> B',
        ]
    )

    outcome = CandidateValidator(fake_runtime, SecurityProfile.STRICT).validate(code, 1)

    assert outcome.runtime.render_valid
    assert fake_runtime.calls == [code]


@pytest.mark.parametrize(
    ("code", "click_line"),
    [
        ('flowchart LR\nA --> B\naccTitle: Diagram " quote\nclick A "#local"', 4),
        ('flowchart LR\nA --> B\naccDescr: Diagram " quote\nclick A "#local"', 4),
        (
            'flowchart LR\nA --> B\naccDescr {\nDiagram " quote\n}\nclick A "#local"',
            6,
        ),
        ('flowchart LR\nA --> B\naccDescr { Diagram " quote }; click A "#local"', 3),
        ('flowchart LR\nA --> B; accTitle: Diagram " quote\nclick A "#local"', 3),
        ('flowchart LR\nA --> B; accDescr: Diagram " quote\nclick A "#local"', 3),
        (
            'flowchart LR\nA --> B; accDescr {\nDiagram " quote\n}\nclick A "#local"',
            5,
        ),
    ],
)
def test_accessibility_quotes_cannot_hide_later_click_statements(fake_runtime, code, click_line):
    outcome = CandidateValidator(fake_runtime, SecurityProfile.STRICT).validate(code, 1)

    assert not outcome.runtime.syntax_valid
    assert outcome.warnings == [f"security:click:line {click_line}"]
    assert fake_runtime.calls == []


def test_accessibility_quotes_remain_plain_text_without_false_statement_boundaries():
    code = (
        'flowchart LR\nA --> B\naccTitle: Diagram " quote; click is title text\n'
        'accDescr {\nDiagram " quote; style is description text\n}\n'
    )

    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(code).safe


def test_non_flowchart_quotes_cannot_hide_later_click_statements(fake_runtime):
    code = (
        'gantt\ntitle Plan " quote\ndateFormat YYYY-MM-DD\nsection Work\n'
        'Task :a, 2024-01-01, 1d\nclick a href "#local"'
    )

    outcome = CandidateValidator(fake_runtime, SecurityProfile.STRICT).validate(code, 1)

    assert not outcome.runtime.syntax_valid
    assert outcome.warnings == ["security:click:line 6"]
    assert fake_runtime.calls == []


@pytest.mark.parametrize(
    ("code", "click_line"),
    [
        (
            'flowchart LR\nsubgraph X\ndirection LR " quote\nA --> B\nend\nclick A "#local"',
            6,
        ),
        ('flowchart LR\nA --> B\nclass A foo"bar\nclick A "#local"', 4),
    ],
)
def test_non_label_flowchart_quotes_cannot_hide_later_click_statements(
    fake_runtime, code, click_line
):
    outcome = CandidateValidator(fake_runtime, SecurityProfile.STRICT).validate(code, 1)

    assert not outcome.runtime.syntax_valid
    assert outcome.warnings == [f"security:click:line {click_line}"]
    assert fake_runtime.calls == []


def test_arbitrary_flowchart_quotes_do_not_rescan_the_whole_statement(monkeypatch):
    class RejectingQuotedLabelPrefix:
        def __init__(self):
            self.calls = 0

        def fullmatch(self, _value):
            self.calls += 1
            return None

    prefix = RejectingQuotedLabelPrefix()
    monkeypatch.setattr(MermaidSecurityScanner, "_quoted_flowchart_label_prefix", prefix)
    code = "flowchart LR\nA" + ('text"' * 5_000) + '\nclick A "#local"'

    report = MermaidSecurityScanner(SecurityProfile.STRICT).scan(code)

    assert not report.safe
    assert any(finding.rule == "click" for finding in report.findings)
    assert prefix.calls == 0


@pytest.mark.integration
def test_non_label_flowchart_quote_regressions_are_active_in_mermaid_11_16():
    codes = [
        ('flowchart LR\nsubgraph X\ndirection LR " quote\nA --> B\nend\nclick A "#local"'),
        'flowchart LR\nA --> B\nclass A foo"bar\nclick A "#local"',
    ]
    runtime = NodeMermaidRuntime()
    try:
        results = [runtime.validate_and_render(code, 20) for code in codes]
    finally:
        runtime.close()

    for code, result in zip(codes, results, strict=True):
        assert result.render_valid, (code, result.error)
        assert "#local" in (result.svg or "")
        assert not MermaidSecurityScanner(SecurityProfile.STRICT).scan(code).safe


def test_resource_limit_does_not_call_runtime(fake_runtime):
    outcome = CandidateValidator(
        fake_runtime, SecurityProfile.STRICT, max_chars=10, max_lines=2
    ).validate("flowchart LR\nA-->B", 1)
    assert not outcome.runtime.syntax_valid
    assert "resource_limit" in outcome.warnings[0]
    assert fake_runtime.calls == []
