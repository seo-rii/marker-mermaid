import pytest

from marker_mermaid.config import SecurityProfile
from marker_mermaid.validation import inspect_svg


def svg(body="", **attributes):
    attrs = {"xmlns": "http://www.w3.org/2000/svg", "viewBox": "0 0 10 10", **attributes}
    return "<svg " + " ".join(f'{key}="{value}"' for key, value in attrs.items()) + f">{body}</svg>"


@pytest.mark.parametrize(
    "css",
    [
        r"fill:url(https://example.invalid/a)",
        r"fill:\75rl(https://example.invalid/a)",
        r'fill:u\72l("https://example.invalid/a")',
        r"fill:url(\68 ttps://example.invalid/a)",
        r'@\69mport "https://example.invalid/a";',
        r"@media screen { .x { fill:\75rl(https://example.invalid/a) } }",
        r"--remote:\75rl(https://example.invalid/a);fill:var(--remote)",
        r'background-image:image-set("https://example.invalid/a" 1x)',
        r"fill:url(https://example.invalid/a",
    ],
)
def test_svg_css_tokens_reject_equivalent_external_references(css):
    assert "rendered SVG contains external CSS" in inspect_svg(
        svg(f"<style>{css}</style>"),
        SecurityProfile.STRICT,
    )


@pytest.mark.parametrize(
    "css",
    [
        "fill:url(#local)",
        r"fill:\75rl(#local)",
        r'fill:url("\23 local")',
        ".x{fill:#fff;stroke:rgb(1,2,3)}",
    ],
)
def test_svg_css_tokens_preserve_local_paint_servers(css):
    assert inspect_svg(svg(f"<style>{css}</style>"), SecurityProfile.STRICT) == []


def test_css_escape_policy_also_applies_to_presentation_attributes():
    assert "rendered SVG contains external CSS" in inspect_svg(
        svg(r'<rect fill="\75rl(https://example.invalid/a)"/>'),
        SecurityProfile.STRICT,
    )


@pytest.mark.parametrize("tag", ["set", "animate", "animateMotion", "animateTransform", "discard"])
@pytest.mark.parametrize("profile", list(SecurityProfile))
def test_dynamic_svg_elements_are_never_static_validated_artifacts(tag, profile):
    assert f"rendered SVG contains forbidden <{tag}>" in inspect_svg(svg(f"<{tag}/>"), profile)


@pytest.mark.parametrize("namespace", ["", "http://example.invalid/svg"])
def test_svg_root_requires_the_svg_namespace(namespace):
    assert "rendered output does not have an SVG root" in inspect_svg(
        svg(xmlns=namespace),
        SecurityProfile.STRICT,
    )


@pytest.mark.parametrize("view_box", ["0 0 0 1", "0 0 1 -1", "0 0 NaN 1", "0 0 1", "0 0 1 1 1"])
def test_svg_viewbox_requires_four_finite_positive_area_components(view_box):
    assert "rendered SVG has an invalid viewBox" in inspect_svg(
        svg(viewBox=view_box),
        SecurityProfile.STRICT,
    )


@pytest.mark.parametrize("width,height", [("1", "1"), ("100%", "20px"), ("2em", "3cm")])
def test_svg_without_viewbox_accepts_positive_intrinsic_dimensions(width, height):
    assert (
        inspect_svg(
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"/>',
            SecurityProfile.STRICT,
        )
        == []
    )


@pytest.mark.parametrize("dimensions", ['width="0" height="1"', 'width="1"', 'height="2"'])
def test_svg_without_viewbox_rejects_missing_or_zero_dimensions(dimensions):
    assert inspect_svg(
        f'<svg xmlns="http://www.w3.org/2000/svg" {dimensions}/>',
        SecurityProfile.STRICT,
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    "body,accepted",
    [
        (r'<rect fill="\75rl(https://example.invalid/a)" width="1" height="1"/>', False),
        (r'<style>@\69mport "https://example.invalid/a";</style>', False),
        ('<set attributeName="href" to="https://example.invalid/a"/>', False),
        (
            '<?xml-stylesheet type="text/css" href="https://example.invalid/a.css"?>'
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><text>x</text></svg>',
            False,
        ),
        ('<defs><linearGradient id="local"/></defs><rect fill="url(#local)"/>', True),
    ],
)
def test_real_worker_preflight_matches_svg_reference_policy(tmp_path, body, accepted):
    import json
    from pathlib import Path

    from marker_mermaid.validation import (
        CandidateValidator,
        NodeMermaidRuntime,
        default_runtime_dir,
    )

    packaged = Path(__file__).resolve().parents[1] / "src/marker_mermaid/runtime"
    dependencies = default_runtime_dir() / "node_modules"
    assert dependencies.is_dir(), "browser CI requires installed runtime dependencies"
    # Fault-inject renderer output while retaining the real worker parser,
    # preflight, Chromium context and Python artifact validator.
    injected_svg = body if body.startswith("<?") else svg(body)
    worker = (
        (packaged / "worker.mjs")
        .read_text()
        .replace(
            "const rendered = await mermaid.render(`mmx-${id}`, code);",
            "const rendered = "
            + json.dumps({"svg": injected_svg, "diagramType": "flowchart"})
            + ";",
        )
    )
    (tmp_path / "worker.mjs").write_text(worker)
    (tmp_path / "render_limits.mjs").write_bytes((packaged / "render_limits.mjs").read_bytes())
    (tmp_path / "node_modules").symlink_to(dependencies, target_is_directory=True)
    runtime = NodeMermaidRuntime(tmp_path)
    try:
        if body.startswith("<?"):
            preflight = runtime.validate_and_render("flowchart LR\nA-->B\n", 20)
            assert preflight.render_valid
            assert preflight.png is None
            assert "processing instruction" in (preflight.png_omitted_reason or "")
        outcome = CandidateValidator(runtime, SecurityProfile.STRICT).validate(
            "flowchart LR\nA-->B\n",
            20,
        )
    finally:
        runtime.close()
    assert outcome.runtime.render_valid is accepted, outcome.runtime.error
    if not accepted:
        assert any("SVG" in warning for warning in outcome.warnings)


@pytest.mark.parametrize(
    "instruction",
    [
        '<?xml-stylesheet type="text/css" href="https://example.invalid/a.css"?>',
        "<?unknown data?>",
    ],
)
def test_svg_processing_instructions_are_rejected_even_before_root(instruction):
    assert "rendered SVG contains a processing instruction" in inspect_svg(
        instruction + svg("<text>hello</text>"),
        SecurityProfile.STRICT,
    )


def test_xml_declaration_is_not_a_processing_instruction():
    assert inspect_svg('<?xml version="1.0"?>' + svg(), SecurityProfile.STRICT) == []
