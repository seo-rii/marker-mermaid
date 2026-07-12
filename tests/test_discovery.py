from __future__ import annotations

from PIL import Image, ImageDraw

from marker_mermaid.discovery import (
    DiscoveredSource,
    FragmentCandidate,
    SourceFragment,
    assess_full_page_coverage,
    propose_composite_panels,
    propose_fragment_merges,
    split_composite_figure,
)


def _two_panel_image() -> Image.Image:
    image = Image.new("RGB", (400, 180), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 25, 160, 155), outline="black", width=4)
    draw.rectangle((240, 25, 380, 155), outline="black", width=4)
    draw.text((55, 75), "panel A", fill="black")
    draw.text((275, 75), "panel B", fill="black")
    return image


def test_discovered_source_is_json_serializable_without_image_bytes():
    source = DiscoveredSource(
        source_id="page-1-panel-1",
        anchor_block_id="figure-1",
        kind="panel",
        fragments=[
            SourceFragment(
                fragment_id="fragment-1",
                page_id=1,
                source_block_ids=["figure-1"],
                page_bbox=(10, 20, 410, 200),
                crop_bbox=(0, 0, 200, 180),
                image_size=(200, 180),
            )
        ],
        signals=["whitespace_separator"],
        confidence=0.8,
    )

    payload = source.model_dump(mode="json")

    assert payload["kind"] == "panel"
    assert payload["fragments"][0]["source_block_ids"] == ["figure-1"]
    assert "image" not in payload


def test_discovered_source_preserves_multi_page_fragment_transforms():
    source = DiscoveredSource(
        source_id="continued-diagram",
        anchor_block_id="figure-1",
        kind="merged",
        fragments=[
            SourceFragment(
                fragment_id="top",
                page_id=1,
                source_block_ids=["figure-1"],
                page_bbox=(10, 300, 390, 500),
                image_size=(380, 200),
            ),
            SourceFragment(
                fragment_id="bottom",
                page_id=2,
                source_block_ids=["figure-2"],
                page_bbox=(10, 0, 390, 220),
                image_size=(380, 220),
                canvas_offset=(0, 200),
            ),
        ],
        signals=["continued", "boundary_touch"],
        confidence=0.9,
    )

    assert source.fragments[1].canvas_offset == (0.0, 200.0)
    assert [fragment.page_id for fragment in source.fragments] == [1, 2]


def test_composite_panel_proposal_uses_whitespace_and_components():
    image = _two_panel_image()

    proposal = propose_composite_panels(image, use_opencv=False)

    assert proposal is not None
    assert proposal.component_backend == "python"
    assert len(proposal.panels) == 2
    assert proposal.signals == ["whitespace_separator", "connected_component_groups"]
    assert proposal.separators[0][0] == "vertical"
    assert 160 < proposal.separators[0][1] < 240
    crops = split_composite_figure(image, proposal)
    assert [crop.size for _, crop in crops] == [(200, 180), (200, 180)]


def test_composite_panel_proposal_can_use_separator_line():
    image = Image.new("RGB", (320, 160), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((15, 30, 125, 130), outline="black", width=3)
    draw.rectangle((195, 30, 305, 130), outline="black", width=3)
    draw.line((160, 0, 160, 159), fill="black", width=2)

    proposal = propose_composite_panels(image, use_opencv=False)

    assert proposal is not None
    assert "line_separator" in proposal.signals
    assert len(proposal.panels) == 2


def test_single_connected_figure_is_not_split():
    image = Image.new("RGB", (240, 120), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 220, 100), outline="black", width=4)

    assert propose_composite_panels(image, use_opencv=False) is None


def test_adjacent_fragments_with_shared_caption_are_proposed():
    fragments = [
        FragmentCandidate(
            block_id="a", bbox=(20, 20, 190, 180), page_size=(400, 300), caption="Figure 1"
        ),
        FragmentCandidate(
            block_id="b", bbox=(198, 20, 380, 180), page_size=(400, 300), caption="Figure 1"
        ),
    ]

    proposals = propose_fragment_merges(fragments)

    assert len(proposals) == 1
    assert proposals[0].block_ids == ("a", "b")
    assert proposals[0].bbox == (20.0, 20.0, 380.0, 180.0)
    assert proposals[0].signals == ["adjacent_bbox", "shared_caption"]


def test_cross_page_continuation_requires_boundary_and_semantic_signal():
    fragments = [
        FragmentCandidate(
            block_id="a",
            bbox=(10, 300, 390, 500),
            page=3,
            page_size=(400, 500),
            caption="Architecture (continued)",
        ),
        FragmentCandidate(
            block_id="b",
            bbox=(10, 0, 390, 220),
            page=4,
            page_size=(400, 500),
            caption="Architecture",
        ),
    ]

    proposals = propose_fragment_merges(fragments)

    assert len(proposals) == 1
    assert proposals[0].pages == (3, 4)
    assert proposals[0].signals == ["shared_caption", "continued", "boundary_touch"]


def test_distant_unrelated_fragments_are_not_merged():
    fragments = [
        FragmentCandidate(block_id="a", bbox=(10, 10, 100, 100), page_size=(500, 500)),
        FragmentCandidate(block_id="b", bbox=(350, 350, 450, 450), page_size=(500, 500)),
    ]

    assert propose_fragment_merges(fragments) == []


def test_adjacent_unrelated_fragments_are_not_merged():
    fragments = [
        FragmentCandidate(block_id="a", bbox=(10, 10, 200, 180), page_size=(500, 500)),
        FragmentCandidate(block_id="b", bbox=(205, 10, 400, 180), page_size=(500, 500)),
    ]

    assert propose_fragment_merges(fragments) == []


def test_full_page_coverage_requires_area_and_close_edges():
    full = assess_full_page_coverage((5, 5, 995, 995), (0, 0, 1000, 1000))
    inset = assess_full_page_coverage((100, 0, 1000, 1000), (0, 0, 1000, 1000))

    assert full.is_full_page
    assert full.area_ratio == 0.9801
    assert not inset.is_full_page
    assert inset.area_ratio == 0.9


def test_full_page_coverage_clips_candidate_overscan():
    coverage = assess_full_page_coverage((-5, -5, 1005, 1005), (0, 0, 1000, 1000))

    assert coverage.is_full_page
    assert coverage.area_ratio == 1.0
