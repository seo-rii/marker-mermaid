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
        ('<text style="font:0px sans-serif">Start forged</text>', None),
        ('<g style="font-size-adjust:0"><text>Start forged</text></g>', None),
        (
            '<style>:link{display:none}</style><a href="#label"><text>Start forged</text></a>',
            None,
        ),
        (
            '<style>:any-link{display:none}</style><a href="#label"><text>Start forged</text></a>',
            None,
        ),
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
        assert candidate.aggregate_score is None
        assert not result.publish
        assert result.review_required
        assert any("projection is unavailable" in message for message in candidate.warnings)
    else:
        assert candidate.scores["ocr_recall"] == expected_recall


@pytest.mark.parametrize(
    "attribute,value",
    [
        ("fill-opacity", "0.00"),
        ("font-size", "0px"),
        ("font-size", "0%"),
        ("fill", "transparent"),
        ("fill", "rgba(0,0,0,0)"),
        ("fill", "none"),
        ("content-visibility", "hidden"),
    ],
)
def test_simple_inherited_paint_cues_do_not_credit_hidden_text(attribute, value):
    assert svg_visible_texts(svg(f'<g {attribute}="{value}"><text>hidden</text></g>')) == []


def test_transparent_fill_does_not_hide_a_visible_text_stroke():
    assert svg_visible_texts(
        svg(
            '<text fill="none" stroke="black">visible</text>'
            '<text fill="none" stroke="black" stroke-width="0">hidden</text>',
        )
    ) == ["visible"]


def test_zero_inherited_font_can_be_restored_with_an_absolute_child_size():
    assert svg_visible_texts(
        svg(
            '<g font-size="0"><text>hidden</text><text font-size="12px">visible</text></g>',
        )
    ) == ["visible"]


@pytest.mark.parametrize(
    "attribute,value",
    [
        ("clip-path", "url(#clip)"),
        ("mask", "url(#mask)"),
        ("filter", "url(#blur)"),
        ("content-visibility", "auto"),
        ("fill", "var(--paint)"),
        ("font-size", "calc(1px - 1px)"),
    ],
)
def test_unsupported_text_paint_on_ancestor_is_unavailable(attribute, value):
    assert svg_visible_texts(svg(f'<g {attribute}="{value}"><text>uncertain</text></g>')) is None


@pytest.mark.parametrize(
    "rule",
    [
        "text:hover{display:none}",
        "text:not(:focus){opacity:0}",
        "text:target{visibility:hidden}",
        r"text:\68 over{display:none}",
    ],
)
def test_dynamic_visibility_selectors_are_not_silently_ignored(rule):
    assert svg_visible_texts(svg(f"<style>{rule}</style><text>uncertain</text>")) is None


def test_all_shorthand_is_unavailable_instead_of_assuming_independent_properties():
    assert (
        svg_visible_texts(
            svg(
                '<g visibility="hidden">'
                '<text visibility="visible" style="all:inherit">hidden</text></g>',
            )
        )
        is None
    )


def test_shape_only_hover_opacity_does_not_invalidate_mermaid_pie_text():
    assert svg_visible_texts(
        svg("""
        <style>.pieCircle:hover { opacity:1 }</style>
        <circle class="pieCircle" r="5"/><text>visible</text>
    """)
    ) == ["visible"]


def test_negated_dynamic_selector_cannot_hide_text_without_unavailable_evidence():
    assert (
        svg_visible_texts(
            svg("""
        <style>g:has(text:hover) { opacity:0 }</style><g><text>uncertain</text></g>
    """)
        )
        is None
    )


@pytest.mark.parametrize("declaration", ["font:0px sans-serif", "font-size-adjust:0"])
@pytest.mark.parametrize(
    "template",
    [
        '<text style="{declaration}">hidden</text>',
        '<g style="{declaration}"><text>hidden</text></g>',
        '<style>text{{{declaration}}}</style><text>hidden</text>',
    ],
)
def test_unsupported_font_properties_do_not_claim_painted_text(declaration, template):
    assert svg_visible_texts(svg(template.format(declaration=declaration))) is None


@pytest.mark.parametrize("selector", [":link", ":any-link", r":\61ny-link"])
@pytest.mark.parametrize("attribute", ['href="#label"', 'xlink:href="#label"'])
def test_svg_link_visibility_selectors_do_not_silently_miss_svg_anchors(selector, attribute):
    body = (
        f'<style>{selector}{{display:none}}</style>'
        '<a xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'{attribute}><text>hidden</text></a>'
    )
    assert svg_visible_texts(svg(body)) is None


def test_link_or_font_rules_on_shapes_do_not_invalidate_unrelated_text():
    assert svg_visible_texts(
        svg(
            '<style>:any-link{display:none}.shape{font:0px sans-serif}</style>'
            '<a href="#shape"><circle r="2"/></a>'
            '<circle class="shape" r="2"/><text>visible</text>'
        )
    ) == ["visible"]


@pytest.mark.integration
def test_chromium_hidden_font_and_link_counterexamples_withhold_ocr_evidence():
    import json
    import subprocess

    from marker_mermaid.validation import default_runtime_dir

    bodies = [
        '<text style="font:0px sans-serif">hidden</text>',
        '<g style="font-size-adjust:0"><text>hidden</text></g>',
        '<style>:link{display:none}</style><a href="#label"><text>hidden</text></a>',
        '<style>:any-link{display:none}</style><a href="#label"><text>hidden</text></a>',
    ]
    script = """
        import { pathToFileURL } from 'node:url';
        const { chromium } = await import(pathToFileURL(process.argv[1]));
        const browser = await chromium.launch({headless:true, args:['--no-sandbox']});
        try {
            const page = await browser.newPage();
            const boxes = [];
            for (const svg of JSON.parse(process.argv[2])) {
                await page.setContent(svg);
                boxes.push(await page.locator('text').evaluate(element => {
                    const box = element.getBBox();
                    return [box.width, box.height];
                }));
            }
            console.log(JSON.stringify(boxes));
        } finally { await browser.close(); }
    """
    rendered_svgs = [svg(body) for body in bodies]
    result = subprocess.run(
        [
            "node", "--input-type=module", "-e", script,
            str(default_runtime_dir().resolve() / "node_modules/playwright/index.mjs"),
            json.dumps(rendered_svgs),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [[0, 0]] * len(bodies)
    assert all(svg_visible_texts(rendered) is None for rendered in rendered_svgs)
