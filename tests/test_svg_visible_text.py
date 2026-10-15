import pytest

from marker_mermaid.scoring import ocr_recall, svg_visible_texts


def svg(body):
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">{body}</svg>'


@pytest.mark.parametrize("opacity", ["0", "0.0", "0.00", "+0", "-0", "0e9", "0%", "0.00%", "-1"])
def test_all_numeric_zero_opacities_suppress_painted_text(opacity):
    assert svg_visible_texts(svg(f'<text opacity="{opacity}">hidden</text>')) == []


@pytest.mark.parametrize("declaration", ["display:none", "visibility:hidden", "opacity:0.00"])
def test_hidden_tspan_is_not_counted_as_visible_ocr_evidence(declaration):
    texts = svg_visible_texts(
        svg(
            f'<text>Start<tspan style="{declaration}">forged</tspan> End</text>',
        )
    )
    assert texts is not None
    assert ocr_recall(["forged"], "", generated_texts=texts) == 0
    assert ocr_recall(["Start End"], "", generated_texts=texts) == 1


def test_nested_text_tails_belong_to_parent_visibility_and_keep_order():
    texts = svg_visible_texts(
        svg(
            '<text>A<tspan>B<tspan display="none">hidden</tspan>C</tspan>D</text>',
        )
    )
    assert texts == ["AB CD"]


def test_visibility_can_be_restored_by_descendant_but_display_and_opacity_cannot():
    assert svg_visible_texts(
        svg(
            '<text visibility="hidden">hidden<tspan visibility="visible">shown</tspan>hidden</text>'
            '<text display="none"><tspan display="inline">hidden</tspan></text>'
            '<text opacity="0"><tspan opacity="1">hidden</tspan></text>',
        )
    ) == [" shown"]


def test_stylesheet_selectors_and_important_inline_cascade_are_respected():
    texts = svg_visible_texts(
        svg("""
        <style>
          .hide { display: none; }
          #kept { display: inline; }
          .important { display:none !important; }
          g > text.zero { opacity:0.00; }
        </style>
        <text class="hide">hidden</text>
        <text id="kept" class="hide">kept</text>
        <text class="important" style="display:inline">hidden</text>
        <text class="hide" style="display:inline !important">override</text>
        <g><text class="zero">hidden</text></g>
    """)
    )
    assert texts == ["kept", "override"]


def test_escaped_css_visibility_values_and_attribute_overrides_are_parsed():
    assert svg_visible_texts(
        svg(r"""
        <style>.hide { d\69splay:n\6fne; }</style>
        <text class="hide">hidden</text>
        <text display="none" style="display:inline">shown</text>
    """)
    ) == ["shown"]


@pytest.mark.parametrize(
    "body",
    [
        "<style>@media screen { text { display:none } }</style><text>uncertain</text>",
        "<style>text { opacity:var(--amount) }</style><text>uncertain</text>",
        '<text style="opacity:calc(1 - 1)">uncertain</text>',
        '<text>uncertain<set attributeName="display" to="none"/></text>',
        '<use href="#label"/>',
    ],
)
def test_unsupported_visibility_models_are_unavailable_instead_of_overcredited(body):
    assert svg_visible_texts(svg(body)) is None


def test_hidden_span_does_not_join_visible_fragments_into_a_forged_token():
    texts = svg_visible_texts(svg('<text>pass<tspan display="none">word</tspan>code</text>'))
    assert ocr_recall(["passcode"], "", generated_texts=texts) == 0


def test_mermaid_stroke_animation_keyframes_do_not_hide_static_labels():
    assert svg_visible_texts(
        svg("""
        <style>@keyframes dash { to { stroke-dashoffset: 0; } }</style>
        <text>shown</text>
    """)
    ) == ["shown"]


def test_visibility_changing_keyframes_and_conditional_styles_remain_unavailable():
    assert (
        svg_visible_texts(
            svg("""
        <style>@keyframes hide { to { opacity: 0; } }</style><text>uncertain</text>
    """)
        )
        is None
    )
    assert (
        svg_visible_texts(
            svg("""
        <style media="print">text { display: inline; }</style><text display="none">hidden</text>
    """)
        )
        is None
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    "code,expected",
    [
        ('flowchart LR\nA["Start"]-->B["Review payment"]\n', "Start Review payment"),
        ("sequenceDiagram\nAlice->>Bob: Hello\n", "Alice Bob Hello"),
        ('pie\n"Alpha":1\n"Beta":2\n', "Alpha Beta"),
    ],
)
def test_real_mermaid_styles_keep_visible_labels_available(code, expected):
    from marker_mermaid.validation import NodeMermaidRuntime

    runtime = NodeMermaidRuntime()
    try:
        rendered = runtime.validate_and_render(code, 20)
    finally:
        runtime.close()
    assert rendered.render_valid, rendered.error
    texts = svg_visible_texts(rendered.svg)
    assert texts is not None
    assert ocr_recall([expected], "", generated_texts=texts) == 1


@pytest.mark.parametrize(
    "body,expected_recall",
    [
        ('<text>Start<tspan display="none">forged</tspan></text>', 0.5),
        (
            '<style>.hide{display:none}</style><text>Start</text><text class="hide">forged</text>',
            0.5,
        ),
        ('<text style="opacity:var(--amount)">Start forged</text>', None),
    ],
)
def test_direct_pipeline_uses_visibility_evidence_or_reports_unavailable(body, expected_recall):
    from PIL import Image

    from marker_mermaid.config import MermaidConfig, PublishPolicy
    from marker_mermaid.engines import JsonFixtureEngine
    from marker_mermaid.models import (
        DiagramTypePrediction,
        DirectMermaidCandidate,
        EngineObservation,
    )
    from marker_mermaid.pipeline import ReconstructionPipeline
    from marker_mermaid.protocols import RuntimeResult
    from marker_mermaid.validation import CandidateValidator

    class Runtime:
        def validate_and_render(self, code, timeout_seconds):
            return RuntimeResult(True, True, diagram_type="flowchart-v2", svg=svg(body))

        def close(self):
            pass

    config = MermaidConfig(
        candidate_count=1,
        enable_fusion=False,
        enable_typed_ir=False,
        enable_generic_scene_ir=False,
        enable_direct_mermaid=True,
        max_repair_iterations=0,
        publish_policy=PublishPolicy.BEST_EFFORT_VALIDATED,
    )
    observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1]),
        direct_candidates=[
            DirectMermaidCandidate(
                diagram_type="flowchart",
                code='flowchart LR\nA["Start forged"]\n',
            )
        ],
    )
    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(Runtime(), config.security_profile),
    ).reconstruct("source", "source.png", Image.new("RGB", (30, 20)), ocr_texts=["Start forged"])
    candidates = [item for item in [result.selected, *result.alternatives] if item is not None]
    assert len(candidates) == 1
    candidate = candidates[0]
    if expected_recall is None:
        assert "ocr_recall" not in candidate.scores
        assert not result.publish
        assert any("projection is unavailable" in message for message in candidate.warnings)
    else:
        assert candidate.scores["ocr_recall"] == expected_recall
