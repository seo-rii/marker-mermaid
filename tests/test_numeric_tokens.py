from decimal import localcontext

import pytest

from marker_mermaid.scoring import numeric_consistency, numeric_token_multiset


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("1", "1.0"),
        ("1", "1e0"),
        ("1e3", "1000"),
        ("−5", "-5"),
        ("１.０", "1"),
        ("-0.00", "0"),
        ("+2E−3", "0.002"),
        ("20.0%", "2e1%"),
    ],
)
def test_numeric_tokens_canonicalize_exact_values(left, right):
    assert numeric_token_multiset([left]) == numeric_token_multiset([right])
    assert numeric_consistency([left], f'pie\n"Value": {right}\n') == 1


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("1e3", "1e9"),
        ("−5", "5"),
        ("20%", "20"),
        ("5kg", "5"),
        ("5kg", "5ms"),
        ("1,000", "1000"),
        ("1,5", "1.5"),
        ("1,000", "1 000"),
    ],
)
def test_numeric_tokens_preserve_meaningful_differences(left, right):
    assert numeric_token_multiset([left]) != numeric_token_multiset([right])
    assert numeric_consistency([left], f'pie\n"Value": {right}\n') < 1


@pytest.mark.parametrize("text", ["1,000", "1,5", "-1.000,50"])
def test_ambiguous_locale_notation_never_earns_consistency_credit(text):
    assert numeric_consistency([text], f'pie\n"{text}": 1\n') == 0


def test_mermaid_array_commas_are_grammar_separators():
    assert numeric_consistency(["1 2 -3 0.4"], "xychart-beta\nline [1,2,-3,0.4]\n") == 1


def test_exact_canonicalization_does_not_round_using_decimal_context():
    with localcontext() as context:
        context.prec = 3
        assert numeric_token_multiset(["123456789012345678901234567890"]) == (
            numeric_token_multiset(["1.23456789012345678901234567890e29"])
        )
        assert numeric_token_multiset(["123456789012345678901234567891"]) != (
            numeric_token_multiset(["123456789012345678901234567890"])
        )


def test_number_units_and_percent_occurrences_remain_distinct():
    assert numeric_token_multiset(["2kg 2kg 20%"]).total() == 3
    assert numeric_consistency(["2kg 2kg 20%"], 'pie\n"2kg 2kg": 20%\n') == 1


@pytest.mark.parametrize("swapped,expected", [(False, 1), (True, 0)])
def test_pie_record_binding_preserves_source_number_spelling_and_exact_values(swapped, expected):
    from PIL import Image

    from marker_mermaid.config import MermaidConfig
    from marker_mermaid.engines import JsonFixtureEngine
    from marker_mermaid.models import (
        DiagramTypePrediction,
        EngineObservation,
        TypedIRCandidate,
        VisualEvidence,
    )
    from marker_mermaid.pipeline import ReconstructionPipeline
    from marker_mermaid.protocols import RuntimeResult
    from marker_mermaid.validation import CandidateValidator

    class Runtime:
        def validate_and_render(self, code, timeout_seconds):
            return RuntimeResult(
                True,
                True,
                diagram_type="pie" if code.startswith("pie") else "flowchart-v2",
                svg=(
                    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50">'
                    '<text>A B</text></svg>'
                ),
            )

        def close(self):
            pass

    config = MermaidConfig(candidate_count=1, enable_fusion=False, max_repair_iterations=0)
    observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["pie"], scores=[1]),
        typed_candidates=[
            TypedIRCandidate(
                diagram_type="pie",
                ir={
                    "slices": [
                        {"label": "A", "value": 20, "bbox": [0, 0, 40, 20], "evidence_ids": ["a"]},
                        {"label": "B", "value": 80, "bbox": [50, 0, 90, 20], "evidence_ids": ["b"]},
                    ]
                },
            )
        ],
        evidence=[
            VisualEvidence(
                id="a",
                kind="ocr_token",
                text="A " + ("8e1" if swapped else "2e1"),
                bbox=(5, 5, 35, 15),
            ),
            VisualEvidence(
                id="b",
                kind="ocr_token",
                text="B " + ("2.0e1" if swapped else "8.0e1"),
                bbox=(55, 5, 85, 15),
            ),
        ],
    )
    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(Runtime(), config.security_profile),
    ).reconstruct("source", "source.png", Image.new("RGB", (100, 50)))
    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == expected
