from __future__ import annotations

import pytest

from marker_mermaid.config import MermaidConfig, PublishPolicy, SecurityProfile
from marker_mermaid.models import MermaidCandidate
from marker_mermaid.scoring import (
    aggregate_scores,
    decide_publication,
    numeric_consistency,
    ocr_recall,
    semantic_score,
)
from marker_mermaid.security import MermaidSecurityScanner
from marker_mermaid.validation import CandidateValidator, inspect_svg


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


def test_style_only_allows_local_style_but_not_remote_css():
    scanner = MermaidSecurityScanner(SecurityProfile.STYLE_ONLY)
    assert scanner.scan("flowchart LR\nA-->B\nstyle A fill:#fff").safe
    assert not scanner.scan("flowchart LR\nA-->B\nstyle A fill:url(http://x)").safe


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


def test_resource_limit_does_not_call_runtime(fake_runtime):
    outcome = CandidateValidator(
        fake_runtime, SecurityProfile.STRICT, max_chars=10, max_lines=2
    ).validate("flowchart LR\nA-->B", 1)
    assert not outcome.runtime.syntax_valid
    assert "resource_limit" in outcome.warnings[0]
    assert fake_runtime.calls == []
