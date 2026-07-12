from __future__ import annotations

from dataclasses import dataclass, field

from PIL import Image, ImageDraw

from marker_mermaid.config import MermaidConfig
from marker_mermaid.marker_discovery import MarkerSourceDiscovery, discover_marker_sources
from marker_mermaid.page_detector import DiagramRegion, PageDiagramDetection
from marker_mermaid.source_assembly import assemble_discovered_source


class Identifier:
    def __init__(self, value: str):
        self.value = value

    def __str__(self) -> str:
        return self.value

    def to_path(self) -> str:
        return self.value.strip("/").replace("/", "_")


@dataclass
class Polygon:
    bbox: tuple[float, float, float, float]


@dataclass
class Block:
    name: str
    bbox: tuple[float, float, float, float]
    image: Image.Image
    page_id: int
    caption: str | None = None
    block_type: str = "Figure"
    current_children: list[object] = field(default_factory=list)

    def __post_init__(self):
        self.id = Identifier(self.name)
        self.polygon = Polygon(self.bbox)

    def get_image(self, document, highres=True):
        return self.image


@dataclass
class Page:
    page_id: int
    bbox: tuple[float, float, float, float]
    structured: list[Block]
    current_children: list[object] = field(default_factory=list)
    image: Image.Image | None = None

    def __post_init__(self):
        self.polygon = Polygon(self.bbox)

    def contained_blocks(self, document, block_types):
        return list(self.structured)

    def get_image(self, document, highres=True):
        return self.image


class Document:
    def __init__(self, pages: list[Page], loose: dict[str, Block] | None = None):
        self.pages = pages
        self.loose = loose or {}

    def get_block(self, reference):
        return self.loose.get(str(reference))


@dataclass
class Caption:
    name: str
    text: str
    block_type: str = "Caption"

    def __post_init__(self):
        self.id = Identifier(self.name)


def _diagram(size=(200, 100), color="white") -> Image.Image:
    image = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 10, size[0] - 10, size[1] - 10), outline="black", width=3)
    return image


def _two_panels() -> Image.Image:
    image = Image.new("RGB", (400, 180), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 25, 160, 155), outline="black", width=4)
    draw.rectangle((240, 25, 380, 155), outline="black", width=4)
    return image


def test_original_registry_keeps_metadata_separate_from_image_payload():
    block = Block("/page/0/Figure/1", (10, 20, 210, 120), _diagram(), page_id=0)
    document = Document([Page(0, (0, 0, 300, 200), [block])])

    result = discover_marker_sources(document, MermaidConfig(split_composite_figures=False))

    source_id = "page_0_Figure_1"
    source = result.registry[source_id]
    assert source.kind == "original"
    assert source.anchor_block_id == "/page/0/Figure/1"
    assert source.fragments[0].page_bbox == (10.0, 20.0, 210.0, 120.0)
    assert source.fragments[0].crop_bbox == (0.0, 0.0, 200.0, 100.0)
    assert source.fragments[0].image_size == (200, 100)
    assert source.fragments[0].canvas_offset == (0.0, 0.0)
    assert result.images[source.fragments[0].fragment_id].size == (200, 100)
    assert "image" not in source.model_dump(mode="json")


def test_full_page_block_is_classified_without_duplicating_original():
    block = Block("/page/2/Picture/1", (0, 0, 500, 700), _diagram((500, 700)), page_id=2)
    document = Document([Page(2, (0, 0, 500, 700), [block])])

    result = discover_marker_sources(document, MermaidConfig(split_composite_figures=False))

    assert len(result.registry) == 1
    source = next(iter(result.registry.values()))
    assert source.kind == "full_page"
    assert "full_page_coverage" in source.signals


def test_unstructured_current_children_objects_and_references_are_not_missed():
    structured = Block("/page/0/Figure/1", (0, 0, 100, 80), _diagram((100, 80)), 0)
    loose_object = Block("/page/0/Picture/2", (110, 0, 210, 80), _diagram((100, 80)), 0)
    loose_reference = Block("/page/0/ComplexRegion/3", (220, 0, 320, 80), _diagram((100, 80)), 0)
    page = Page(
        0,
        (0, 0, 400, 200),
        [structured],
        current_children=[structured, loose_object, loose_reference.id],
    )

    result = discover_marker_sources(
        Document([page], {str(loose_reference.id): loose_reference}),
        MermaidConfig(split_composite_figures=False),
    )

    assert {source.anchor_block_id for source in result.registry.values()} == {
        "/page/0/Figure/1",
        "/page/0/Picture/2",
        "/page/0/ComplexRegion/3",
    }


def test_composite_panels_keep_unsplit_source_and_exact_coordinate_mapping():
    block = Block("/page/1/Figure/4", (100, 50, 500, 230), _two_panels(), page_id=1)
    document = Document([Page(1, (0, 0, 600, 300), [block])])

    result = discover_marker_sources(document)

    originals = [source for source in result.registry.values() if source.kind == "original"]
    panels = [source for source in result.registry.values() if source.kind == "panel"]
    assert len(originals) == 1
    assert len(panels) == 2
    first = panels[0]
    fragment = first.fragments[0]
    assert first.anchor_block_id == "/page/1/Figure/4"
    assert fragment.crop_bbox == (0.0, 0.0, 200.0, 180.0)
    assert fragment.page_bbox == (100.0, 50.0, 300.0, 230.0)
    assert fragment.image_size == (400, 180)
    assert result.images[fragment.fragment_id].size == fragment.image_size
    assert assemble_discovered_source(first, result.images).image.size == (200, 180)


def test_same_page_captioned_fragments_become_positioned_merged_source():
    left = Block("/page/0/Figure/1", (20, 20, 190, 120), _diagram((170, 100), "red"), 0, "Figure 1")
    right = Block(
        "/page/0/Figure/2", (198, 20, 380, 120), _diagram((182, 100), "blue"), 0, "Figure 1"
    )
    document = Document([Page(0, (0, 0, 400, 300), [left, right])])

    result = discover_marker_sources(document, MermaidConfig(split_composite_figures=False))

    merged = next(source for source in result.registry.values() if source.kind == "merged")
    assert merged.anchor_block_id == "/page/0/Figure/1"
    assert [fragment.page_bbox for fragment in merged.fragments] == [
        (20.0, 20.0, 190.0, 120.0),
        (198.0, 20.0, 380.0, 120.0),
    ]
    assert [fragment.image_size for fragment in merged.fragments] == [(170, 100), (182, 100)]
    assert [fragment.canvas_offset for fragment in merged.fragments] == [
        (0.0, 0.0),
        (178.0, 0.0),
    ]
    assert assemble_discovered_source(merged, result.images).image.size == (360, 100)


def test_cross_page_continuation_stacks_images_and_preserves_page_geometry():
    first = Block(
        "/page/3/Figure/1",
        (10, 300, 390, 500),
        _diagram((380, 200), "red"),
        3,
        "Architecture (continued)",
    )
    second = Block(
        "/page/4/Figure/1",
        (10, 0, 390, 220),
        _diagram((380, 220), "blue"),
        4,
        "Architecture",
    )
    document = Document([Page(3, (0, 0, 400, 500), [first]), Page(4, (0, 0, 400, 500), [second])])

    result = discover_marker_sources(document, MermaidConfig(split_composite_figures=False))

    merged = next(source for source in result.registry.values() if source.kind == "merged")
    assert merged.anchor_block_id == "/page/3/Figure/1"
    assert [fragment.page_id for fragment in merged.fragments] == [3, 4]
    assert [fragment.page_bbox for fragment in merged.fragments] == [
        (10.0, 300.0, 390.0, 500.0),
        (10.0, 0.0, 390.0, 220.0),
    ]
    assert [fragment.canvas_offset for fragment in merged.fragments] == [
        (0.0, 0.0),
        (0.0, 200.0),
    ]
    assert assemble_discovered_source(merged, result.images).image.size == (380, 420)


def test_adjacent_or_boundary_touching_fragments_without_captions_never_merge():
    first = Block("/page/0/Figure/1", (0, 0, 200, 180), _diagram((200, 180)), 0)
    second = Block("/page/0/Figure/2", (205, 0, 400, 180), _diagram((195, 180)), 0)
    document = Document([Page(0, (0, 0, 500, 500), [first, second])])

    result = discover_marker_sources(document, MermaidConfig(split_composite_figures=False))

    assert all(source.kind != "merged" for source in result.registry.values())


def test_nested_blocks_with_identical_geometry_and_pixels_are_reconstructed_once():
    image = _diagram((200, 100))
    figure = Block("/page/0/Figure/1", (10, 20, 210, 120), image, 0)
    picture = Block(
        "/page/0/Picture/2",
        (10, 20, 210, 120),
        image.copy(),
        0,
        block_type="Picture",
    )
    document = Document([Page(0, (0, 0, 300, 200), [figure, picture])])

    result = discover_marker_sources(
        document,
        MermaidConfig(split_composite_figures=False),
    )

    assert len(result.registry) == 1
    assert next(iter(result.registry.values())).anchor_block_id == str(figure.id)


def test_panel_failure_keeps_original_and_other_sources(monkeypatch):
    first = Block("/page/0/Figure/1", (0, 0, 200, 100), _diagram(), 0)
    second = Block("/page/0/Figure/2", (220, 0, 420, 100), _diagram(), 0)
    document = Document([Page(0, (0, 0, 500, 200), [first, second])])
    calls = 0

    def fail_first(image):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("bad panel detector")
        return None

    monkeypatch.setattr(
        "marker_mermaid.marker_discovery.propose_composite_panels",
        fail_first,
    )

    result = discover_marker_sources(document)

    assert {source.anchor_block_id for source in result.registry.values()} == {
        str(first.id),
        str(second.id),
    }
    assert result.errors == [
        {
            "stage": "panel_split",
            "source_id": "page_0_Figure_1",
            "error": "RuntimeError: bad panel detector",
        }
    ]


def test_caption_block_references_are_resolved_without_using_diagram_raw_text():
    caption = Caption("/page/0/Caption/1", "Figure 7")
    first = Block("/page/0/Figure/1", (10, 20, 190, 120), _diagram((180, 100)), 0)
    second = Block("/page/0/Figure/2", (195, 20, 380, 120), _diagram((185, 100)), 0)
    first.caption = caption.id
    second.caption = caption.id
    document = Document(
        [Page(0, (0, 0, 400, 300), [first, second])],
        {str(caption.id): caption},
    )

    result = discover_marker_sources(document, MermaidConfig(split_composite_figures=False))

    assert any(source.kind == "merged" for source in result.registry.values())


def test_page_detector_adds_scaled_virtual_crop_and_uses_nearest_existing_anchor():
    anchor = Block("/page/0/Figure/1", (10, 10, 60, 60), _diagram((50, 50)), 0)
    page_image = Image.new("RGB", (640, 480), "white")
    page = Page(0, (0, 0, 320, 240), [anchor], image=page_image)
    calls = []

    def detector(image, occupied, **kwargs):
        calls.append((image.size, occupied, kwargs))
        return PageDiagramDetection(
            page_size=image.size,
            edge_backend="pillow",
            regions=[
                DiagramRegion(
                    region_id="page-diagram-001",
                    bbox=(160, 120, 560, 400),
                    confidence=0.81,
                    signals=["multi_axis_structure"],
                    component_count=4,
                    edge_density=0.08,
                )
            ],
        )

    result = MarkerSourceDiscovery(
        MermaidConfig(split_composite_figures=False),
        page_region_detector=detector,
    ).discover(Document([page]))

    proposal = next(source for source in result.registry.values() if source.kind == "page_proposal")
    fragment = proposal.fragments[0]
    assert proposal.anchor_block_id == str(anchor.id)
    assert proposal.signals == [
        "page_level_detector",
        "pillow_edge_backend",
        "multi_axis_structure",
    ]
    assert fragment.source_block_ids == []
    assert fragment.crop_bbox == (0.0, 0.0, 400.0, 280.0)
    assert fragment.page_bbox == (80.0, 60.0, 280.0, 200.0)
    assert result.images[fragment.fragment_id].size == (400, 280)
    assert calls == [
        (
            (640, 480),
            [(20.0, 20.0, 120.0, 120.0)],
            {"use_opencv": True},
        )
    ]


def test_unanchored_page_proposal_remains_discoverable_without_registry_mutation():
    page = Page(2, (0, 0, 100, 100), [], image=Image.new("RGB", (100, 100), "white"))

    def detector(image, occupied, **kwargs):
        return PageDiagramDetection(
            page_size=image.size,
            edge_backend="pillow",
            regions=[
                DiagramRegion(
                    region_id="page-diagram-001",
                    bbox=(10, 10, 90, 90),
                    confidence=0.7,
                    signals=["multi_axis_structure"],
                    component_count=2,
                    edge_density=0.05,
                )
            ],
        )

    result = MarkerSourceDiscovery(
        MermaidConfig(split_composite_figures=False),
        page_region_detector=detector,
    ).discover(Document([page]))

    [proposal] = list(result.registry.values())
    assert proposal.kind == "page_proposal"
    assert proposal.anchor_block_id is None
    assert proposal.fragments[0].source_block_ids == []


def test_page_detector_failure_is_isolated_from_marker_block_sources():
    anchor = Block("/page/3/Figure/1", (10, 10, 80, 80), _diagram((70, 70)), 3)
    page = Page(3, (0, 0, 100, 100), [anchor], image=Image.new("RGB", (100, 100), "white"))

    def detector(image, occupied, **kwargs):
        raise RuntimeError("broken detector")

    result = MarkerSourceDiscovery(
        MermaidConfig(split_composite_figures=False),
        page_region_detector=detector,
    ).discover(Document([page]))

    assert [source.kind for source in result.registry.values()] == ["original"]
    assert result.errors == [
        {
            "stage": "page_detector",
            "source_id": "page-3",
            "error": "RuntimeError: broken detector",
        }
    ]
