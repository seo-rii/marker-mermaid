from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

from marker_mermaid.protocols import SourceContext
from marker_mermaid.vector import (
    VectorObservation,
    VectorPrimitive,
    VectorPrimitiveEngine,
    VectorText,
    extract_vector_observation,
)


def _context(source: object | None, *, block_ids: list[str] | None = None) -> SourceContext:
    return SourceContext(
        source_id="source",
        source_block_ids=block_ids or ["block-1"],
        source_image_name="source.png",
        image=Image.new("RGB", (200, 100), "white"),
        source_block=source,
    )


def test_vector_observation_assigns_text_colors_and_unambiguous_relation() -> None:
    observation = VectorObservation(
        canvas_size=(200, 100),
        texts=(
            VectorText("Start", (20, 20, 60, 35)),
            VectorText("Finish", (140, 20, 180, 35)),
        ),
        primitives=(
            VectorPrimitive(
                kind="rectangle",
                bbox=(10, 10, 70, 50),
                fill_color="#ffeeaa",
                stroke_color="#112233",
                line_style="thick",
                closed=True,
            ),
            VectorPrimitive(kind="ellipse", bbox=(130, 10, 190, 50), closed=True),
            VectorPrimitive(
                kind="line",
                bbox=(70, 30, 130, 30),
                points=((70, 30), (130, 30)),
                stroke_color="#445566",
                arrow_at_end=True,
            ),
        ),
    )

    result = observation.to_engine_observation(["block-1"])

    assert result.prediction.candidates == ["flowchart", "generic_network"]
    assert result.scene_ir is not None
    assert [element.text for element in result.scene_ir.elements] == ["Start", "Finish"]
    assert result.scene_ir.elements[0].fill_color == "#ffeeaa"
    assert result.scene_ir.elements[0].border_color == "#112233"
    assert result.scene_ir.elements[0].border_style == "thick"
    assert result.scene_ir.relations[0].source_id == "vector-node-001"
    assert result.scene_ir.relations[0].target_id == "vector-node-002"
    assert result.scene_ir.relations[0].arrow_at_end is True
    assert result.scene_ir.relations[0].line_color == "#445566"
    assert result.scene_ir.reading_direction == "LR"
    assert {item.kind for item in result.evidence} == {
        "contour",
        "line_segment",
        "vector_text",
    }
    assert all(item.source_block_ids == ["block-1"] for item in result.evidence)


def test_ambiguous_nested_shape_does_not_receive_text_or_connector() -> None:
    observation = VectorObservation(
        canvas_size=(100, 100),
        texts=(VectorText("ambiguous", (40, 40, 60, 50)),),
        primitives=(
            VectorPrimitive(kind="rectangle", bbox=(0, 0, 100, 100), closed=True),
            VectorPrimitive(kind="rectangle", bbox=(30, 30, 70, 70), closed=True),
            VectorPrimitive(kind="line", bbox=(50, 50, 100, 50), points=((50, 50), (100, 50))),
        ),
    )

    result = observation.to_engine_observation(["block"])

    assert result.scene_ir is not None
    assert [item.text for item in result.scene_ir.elements] == [None, None]
    assert result.scene_ir.relations == []
    assert any(item.kind == "vector_text" and item.text == "ambiguous" for item in result.evidence)


def test_missing_vector_data_fails_closed() -> None:
    result = VectorPrimitiveEngine().observe(_context(object()))

    assert result.prediction.candidates == ["unknown"]
    assert result.scene_ir is None
    assert result.evidence == []
    assert "no PDF vector primitives or text" in result.warnings[0]


@dataclass
class _Rect:
    x0: float
    y0: float
    x1: float
    y1: float


class _Page:
    def get_text(self, mode: str):
        if mode == "dict":
            return {
                "blocks": [
                    {
                        "lines": [
                            {
                                "spans": [
                                    {
                                        "text": "Node",
                                        "bbox": (110, 210, 150, 225),
                                        "color": 0x123456,
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        return []

    def get_drawings(self):
        return [
            {
                "items": [("re", _Rect(100, 200, 200, 260), 1)],
                "fill": (1.0, 0.5, 0.0),
                "color": (0.0, 0.0, 1.0),
                "width": 2.5,
            },
            {"items": [("l", (200, 230), (300, 230))], "dashes": "[3 2] 0"},
            {"items": [("re", _Rect(400, 400, 450, 450), 1)]},
        ]


class _Block:
    bbox = (100, 200, 300, 300)
    page = _Page()


def test_duck_typed_page_vectors_are_cropped_and_scaled_to_source_image() -> None:
    result = VectorPrimitiveEngine().observe(_context(_Block()))

    assert result.scene_ir is not None
    node = result.scene_ir.elements[0]
    assert node.bbox == (0.0, 0.0, 100.0, 60.0)
    assert node.text == "Node"
    assert node.fill_color == "#ff8000"
    assert node.border_color == "#0000ff"
    assert node.border_style == "thick"
    text_evidence = next(item for item in result.evidence if item.kind == "vector_text")
    assert text_evidence.bbox == (10.0, 10.0, 50.0, 25.0)
    # The out-of-crop rectangle does not leak into the reconstructed crop.
    assert len(result.scene_ir.elements) == 1
    assert any("bbox fallback" in warning for warning in result.warnings)


def test_explicit_pdf_page_provider_takes_precedence_over_marker_block() -> None:
    class Provider(_Page):
        vector_coordinate_space = "page"
        page_id = 3

    context = _context(object(), block_ids=["/page/3/Figure/2"])
    context.vector_sources = [Provider()]
    context.source_mapping = {
        "assembly": {
            "placements": [
                {
                    "page_id": 3,
                    "source_block_ids": ["/page/3/Figure/2"],
                    "page_bbox": [100, 200, 300, 300],
                    "page_to_canvas": [1, 0, -100, 0, 1, -200],
                }
            ]
        }
    }

    result = VectorPrimitiveEngine().observe(context)

    assert result.scene_ir is not None
    assert result.scene_ir.elements[0].bbox == (0.0, 0.0, 100.0, 60.0)
    assert result.scene_ir.elements[0].text == "Node"


def test_assembly_page_to_canvas_mapping_overrides_bbox_fallback() -> None:
    class Block(_Block):
        id = "/page/3/Figure/2"
        page_id = 3

    context = _context(Block(), block_ids=[Block.id])
    context.source_mapping = {
        "assembly": {
            "placements": [
                {
                    "page_id": 2,
                    "source_block_ids": ["other"],
                    "page_bbox": [0, 0, 10, 10],
                    "page_to_canvas": [1, 0, 500, 0, 1, 500],
                },
                {
                    "page_id": 3,
                    "source_block_ids": [Block.id],
                    "page_bbox": [100, 200, 300, 300],
                    "page_to_canvas": [1, 0, -80, 0, 1, -190],
                },
            ]
        }
    }

    result = VectorPrimitiveEngine().observe(context)

    assert result.scene_ir is not None
    assert result.scene_ir.elements[0].bbox == (20.0, 10.0, 120.0, 70.0)
    line = next(item for item in result.evidence if item.kind == "line_segment")
    assert line.bbox == (120.0, 40.0, 220.0, 40.0)
    assert not any("bbox fallback" in warning for warning in result.warnings)


class _GenericProvider:
    vector_texts = [{"text": "A", "bbox": (15, 15, 25, 25)}]
    vector_primitives = [
        {
            "type": "ellipse",
            "bbox": (10, 10, 40, 40),
            "closed": True,
            "fill_color": "RED",
            "stroke_color": (128, 64, 0),
        },
        {"kind": "unsupported-without-bbox"},
    ]


def test_generic_attributes_and_malformed_records_are_supported() -> None:
    observation = extract_vector_observation(_GenericProvider(), (200, 100))
    result = observation.to_engine_observation(["generic"])

    assert result.scene_ir is not None
    assert result.scene_ir.elements[0].shape == "ellipse"
    assert result.scene_ir.elements[0].text == "A"
    assert result.scene_ir.elements[0].fill_color == "red"
    assert result.scene_ir.elements[0].border_color == "#804000"
    assert "ignored 1 malformed vector record(s)" in result.warnings


def test_pymupdf_quad_and_bezier_commands_are_normalized() -> None:
    class Quad:
        ul = (10, 10)
        ur = (50, 10)
        lr = (50, 50)
        ll = (10, 50)

    class Provider:
        def get_drawings(self):
            return [
                {
                    "items": [
                        ("qu", Quad()),
                        ("c", (50, 30), (65, 30), (75, 30), (90, 30)),
                    ]
                }
            ]

    result = VectorPrimitiveEngine().observe(_context(Provider()))

    assert result.scene_ir is not None
    assert result.scene_ir.elements[0].shape == "polygon"
    assert any(item.kind == "line_segment" for item in result.evidence)


def test_source_blocks_are_preferred_for_merged_contexts() -> None:
    observations = {
        "first": VectorObservation(
            canvas_size=(200, 100),
            primitives=(VectorPrimitive(kind="rectangle", bbox=(5, 5, 25, 25), closed=True),),
        ),
        "second": VectorObservation(
            canvas_size=(200, 100),
            primitives=(VectorPrimitive(kind="ellipse", bbox=(100, 5, 125, 25), closed=True),),
        ),
    }
    context = _context("legacy", block_ids=["one", "two"])
    # SourceContext gains this field in the Marker integration; attaching it
    # dynamically keeps this test compatible with the pre-integration model.
    context.source_blocks = ["first", "second"]  # type: ignore[attr-defined]
    seen: list[str] = []

    def extractor(source: str, _size: tuple[int, int]) -> VectorObservation:
        seen.append(source)
        return observations[source]

    result = VectorPrimitiveEngine(extractor=extractor).observe(context)

    assert seen == ["first", "second"]
    assert result.scene_ir is not None
    assert [item.shape for item in result.scene_ir.elements] == ["rectangle", "ellipse"]


def test_reverse_arrow_is_canonicalized_to_source_to_target() -> None:
    observation = VectorObservation(
        canvas_size=(100, 50),
        primitives=(
            VectorPrimitive(kind="rectangle", bbox=(0, 0, 20, 20), closed=True),
            VectorPrimitive(kind="rectangle", bbox=(80, 0, 100, 20), closed=True),
            VectorPrimitive(
                kind="line",
                bbox=(20, 10, 80, 10),
                points=((20, 10), (80, 10)),
                arrow_at_start=True,
            ),
        ),
    )

    result = observation.to_engine_observation(["block"])

    assert result.scene_ir is not None
    relation = result.scene_ir.relations[0]
    assert relation.source_id == "vector-node-002"
    assert relation.target_id == "vector-node-001"
    assert relation.polyline == [(80.0, 10.0), (20.0, 10.0)]
    assert relation.arrow_at_start is False
    assert relation.arrow_at_end is True


def test_primitive_budget_is_enforced() -> None:
    class Provider:
        vector_primitives = [
            {"kind": "rectangle", "bbox": (index * 10, 0, index * 10 + 5, 5)} for index in range(4)
        ]

    observation = extract_vector_observation(Provider(), (200, 100), max_primitives=2)

    assert len(observation.primitives) == 2
    assert "truncated to the configured budget" in observation.warnings[-1]
