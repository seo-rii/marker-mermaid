from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import pytest
from PIL import Image

import marker_mermaid.vector as vector_module
from marker_mermaid.models import (
    MAX_EVIDENCE_REFS,
    MAX_OBSERVATION_EVIDENCE,
    MAX_OBSERVATION_WARNINGS,
    MAX_SCENE_ELEMENTS,
    MAX_TEXT_CHARS,
)
from marker_mermaid.protocols import SourceContext
from marker_mermaid.vector import (
    MAX_VECTOR_POLYGON_POINTS,
    MAX_VECTOR_POLYLINE_POINTS,
    MAX_VECTOR_TOKEN_CHARS,
    MAX_VECTOR_TOTAL_POINTS,
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


def test_vector_approximate_deduplication_has_a_comparison_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vector_module, "MAX_VECTOR_DEDUP_COMPARISONS", 2)
    observation = VectorObservation(
        canvas_size=(100, 100),
        primitives=tuple(
            VectorPrimitive(
                kind="rectangle",
                bbox=(index * 20, 0, index * 20 + 10, 10),
                closed=True,
            )
            for index in range(3)
        ),
    )

    result = observation.to_engine_observation(["source"])

    assert result.scene_ir is not None
    assert len(result.scene_ir.elements) == 3
    assert any("duplicate comparison budget" in warning for warning in result.warnings)


def test_vector_text_matching_has_a_comparison_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vector_module, "MAX_VECTOR_TEXT_MATCH_COMPARISONS", 1)
    observation = VectorObservation(
        canvas_size=(100, 100),
        texts=(
            VectorText("first", (0, 0, 10, 10)),
            VectorText("second", (0, 0, 10, 10)),
        ),
        primitives=(VectorPrimitive(kind="rectangle", bbox=(0, 0, 20, 20), closed=True),),
    )

    result = observation.to_engine_observation(["source"])

    assert result.scene_ir is not None
    assert result.scene_ir.elements[0].text == "first"
    assert any("text-to-node comparison budget" in warning for warning in result.warnings)


def test_vector_endpoint_matching_has_a_comparison_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vector_module, "MAX_VECTOR_ENDPOINT_MATCH_COMPARISONS", 3)
    observation = VectorObservation(
        canvas_size=(100, 100),
        primitives=(
            VectorPrimitive(kind="rectangle", bbox=(0, 0, 20, 20), closed=True),
            VectorPrimitive(kind="rectangle", bbox=(80, 0, 100, 20), closed=True),
            VectorPrimitive(
                kind="line",
                bbox=(20, 10, 80, 10),
                points=((20, 10), (80, 10)),
                arrow_at_end=True,
            ),
        ),
    )

    result = observation.to_engine_observation(["source"])

    assert result.scene_ir is not None
    assert result.scene_ir.relations == []
    assert any("endpoint comparison budget" in warning for warning in result.warnings)


@pytest.mark.parametrize(
    ("text_count", "enriched"),
    [(MAX_EVIDENCE_REFS - 1, True), (MAX_EVIDENCE_REFS, False)],
)
def test_vector_text_enrichment_respects_the_scene_evidence_reference_cap(
    text_count: int,
    enriched: bool,
) -> None:
    observation = VectorObservation(
        canvas_size=(100, 100),
        texts=tuple(
            VectorText(f"token-{index:03d}", (10, 10, 20, 20)) for index in range(text_count)
        ),
        primitives=(VectorPrimitive(kind="rectangle", bbox=(0, 0, 50, 50), closed=True),),
    )

    result = observation.to_engine_observation(["source"])

    assert result.scene_ir is not None
    node = result.scene_ir.elements[0]
    if enriched:
        assert node.text is not None
        assert len(node.evidence_ids) == MAX_EVIDENCE_REFS
        assert not any("text enrichment exceeded" in warning for warning in result.warnings)
    else:
        assert node.text is None
        assert node.evidence_ids == ["vector-shape-001"]
        assert any("text enrichment exceeded" in warning for warning in result.warnings)
    assert len(result.evidence) == text_count + 1


def test_vector_text_enrichment_revalidates_the_combined_scene_text_size() -> None:
    span_size = MAX_TEXT_CHARS // 2 + 1
    observation = VectorObservation(
        canvas_size=(100, 100),
        texts=(
            VectorText("a" * span_size, (10, 10, 20, 20)),
            VectorText("b" * span_size, (10, 20, 20, 30)),
        ),
        primitives=(VectorPrimitive(kind="rectangle", bbox=(0, 0, 50, 50), closed=True),),
    )

    result = observation.to_engine_observation(["source"])

    assert result.scene_ir is not None
    assert result.scene_ir.elements[0].text is None
    assert result.scene_ir.elements[0].evidence_ids == ["vector-shape-001"]
    assert any("text enrichment exceeded" in warning for warning in result.warnings)
    assert len(result.evidence) == 3


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
                                        "flags": 16,
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
    assert node.font_weight == "bold"
    text_evidence = next(item for item in result.evidence if item.kind == "vector_text")
    assert text_evidence.bbox == (10.0, 10.0, 50.0, 25.0)
    assert text_evidence.font_weight == "bold"
    # The out-of-crop rectangle does not leak into the reconstructed crop.
    assert len(result.scene_ir.elements) == 1
    assert any("bbox fallback" in warning for warning in result.warnings)


def test_mixed_vector_span_weights_fail_closed_without_node_emphasis() -> None:
    observation = VectorObservation(
        canvas_size=(100, 50),
        texts=(
            VectorText("Bold", (10, 10, 35, 20), font_weight="bold"),
            VectorText("Normal", (40, 10, 75, 20), font_weight="normal"),
        ),
        primitives=(VectorPrimitive(kind="rectangle", bbox=(0, 0, 90, 30), closed=True),),
    )

    result = observation.to_engine_observation(["block"])

    assert result.scene_ir is not None
    assert result.scene_ir.elements[0].font_weight is None
    assert any("mixed or partial" in warning for warning in result.warnings)


def test_duplicate_vector_span_weight_conflict_does_not_duplicate_the_label() -> None:
    observation = VectorObservation(
        canvas_size=(100, 50),
        texts=(
            VectorText("Node", (10, 10, 50, 20), font_weight="bold"),
            VectorText("Node", (10, 10, 50, 20), font_weight="normal"),
        ),
        primitives=(VectorPrimitive(kind="rectangle", bbox=(0, 0, 90, 30), closed=True),),
    )

    result = observation.to_engine_observation(["block"])

    assert result.scene_ir is not None
    assert result.scene_ir.elements[0].text == "Node"
    assert result.scene_ir.elements[0].font_weight is None
    assert any("duplicate span" in warning for warning in result.warnings)


def test_vector_text_fourth_positional_argument_remains_confidence() -> None:
    text = VectorText("Node", (10, 10, 50, 20), None, 0.8)

    assert text.confidence == 0.8
    assert text.font_weight is None


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


def test_marker_block_id_selects_the_matching_same_page_vector_placement() -> None:
    from marker.schema import BlockTypes
    from marker.schema.blocks.base import BlockId

    marker_id = BlockId(page_id=3, block_type=BlockTypes.Figure, block_id=2)

    class Block(_Block):
        id = marker_id
        page_id = 3

    context = _context(Block(), block_ids=[str(marker_id)])
    context.source_mapping = {
        "assembly": {
            "placements": [
                {
                    "page_id": 3,
                    "source_block_ids": ["/page/3/Figure/1"],
                    "page_bbox": [100, 200, 300, 300],
                    "page_to_canvas": [1, 0, 500, 0, 1, 500],
                },
                {
                    "page_id": 3,
                    "source_block_ids": [str(marker_id)],
                    "page_bbox": [100, 200, 300, 300],
                    "page_to_canvas": [1, 0, -80, 0, 1, -190],
                },
            ]
        }
    }

    result = VectorPrimitiveEngine().observe(context)

    assert result.scene_ir is not None
    assert result.scene_ir.elements[0].bbox == (20.0, 10.0, 120.0, 70.0)
    assert not any("bbox fallback" in warning for warning in result.warnings)


@pytest.mark.parametrize("placement_count", [MAX_EVIDENCE_REFS, MAX_EVIDENCE_REFS + 1])
def test_vector_mapping_placement_limit_is_atomic(placement_count: int) -> None:
    class Block:
        id = "target"
        page_id = 1
        bbox = (0, 0, 100, 100)
        vector_coordinate_space = "page"
        vector_primitives = [
            {"kind": "rectangle", "bbox": (10, 10, 20, 20)},
        ]

    placements = [
        {
            "page_id": 1,
            "source_block_ids": [f"other-{index}"],
            "page_bbox": [0, 0, 100, 100],
            "page_to_canvas": [1, 0, 0, 0, 1, 0],
        }
        for index in range(MAX_EVIDENCE_REFS - 1)
    ]
    placements.append(
        {
            "page_id": 1,
            "source_block_ids": ["target"],
            "page_bbox": [0, 0, 100, 100],
            "page_to_canvas": [1, 0, 10, 0, 1, 10],
        }
    )
    if placement_count > MAX_EVIDENCE_REFS:
        placements.append(
            {
                "page_id": 1,
                "source_block_ids": ["overflow"],
                "page_bbox": [0, 0, 100, 100],
                "page_to_canvas": [1, 0, 20, 0, 1, 20],
            }
        )

    observation = extract_vector_observation(
        Block(),
        (100, 100),
        max_primitives=1,
        max_texts=0,
        max_text_chars=0,
        source_mapping={"assembly": {"placements": placements}},
    )

    assert len(observation.primitives) == 1
    if placement_count == MAX_EVIDENCE_REFS:
        assert observation.primitives[0].bbox == (20.0, 20.0, 30.0, 30.0)
        assert not any("bbox fallback" in warning for warning in observation.warnings)
    else:
        assert observation.primitives[0].bbox == (10.0, 10.0, 20.0, 20.0)
        assert any("bbox fallback" in warning for warning in observation.warnings)


def test_vector_source_mapping_is_resolved_once_across_nested_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Provider:
        vector_primitives: list[object] = []

    class Block:
        id = "target"
        page_id = 1
        bbox = (0, 0, 100, 100)
        vector_coordinate_space = "page"
        vector_primitives: list[object] = []
        page = Provider()
        document_page = Provider()
        page_ref = Provider()

    calls = 0
    original = vector_module._mapping_transform

    def counting_mapping_transform(mapping_index, source):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return original(mapping_index, source)

    monkeypatch.setattr(vector_module, "_mapping_transform", counting_mapping_transform)

    extract_vector_observation(
        Block(),
        (100, 100),
        max_primitives=1,
        max_texts=0,
        max_text_chars=0,
        source_mapping={
            "assembly": {
                "placements": [
                    {
                        "page_id": 1,
                        "source_block_ids": ["target"],
                        "page_bbox": [0, 0, 100, 100],
                        "page_to_canvas": [1, 0, 0, 0, 1, 0],
                    }
                ]
            }
        },
    )

    assert calls == 1


def test_vector_engine_builds_one_mapping_index_for_all_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Block:
        bbox = (0, 0, 100, 100)
        vector_coordinate_space = "page"
        vector_primitives: list[object] = []

        def __init__(self, index: int) -> None:
            self.id = f"block-{index}"
            self.page_id = index

    sources = [Block(index) for index in range(MAX_EVIDENCE_REFS)]
    placements = [
        {
            "page_id": index,
            "source_block_ids": [f"block-{index}"],
            "page_bbox": [0, 0, 100, 100],
            "page_to_canvas": [1, 0, 0, 0, 1, 0],
        }
        for index in range(MAX_EVIDENCE_REFS)
    ]
    context = _context(None)
    context.vector_sources = sources
    context.source_mapping = {"assembly": {"placements": placements}}
    calls = 0
    original = vector_module._build_vector_mapping_index

    def counting_index(source_mapping):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return original(source_mapping)

    monkeypatch.setattr(vector_module, "_build_vector_mapping_index", counting_index)

    result = VectorPrimitiveEngine(
        max_primitives=1,
        max_texts=1,
        max_text_chars=1,
    ).observe(context)

    assert calls == 1
    assert result.scene_ir is None


def test_custom_vector_extractor_does_not_build_a_mapping_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(_source_mapping):
        raise AssertionError("custom extractors must not build the built-in mapping index")

    monkeypatch.setattr(vector_module, "_build_vector_mapping_index", fail_if_called)

    result = VectorPrimitiveEngine(
        extractor=lambda _source, size: VectorObservation(canvas_size=size),
    ).observe(_context(object()))

    assert result.scene_ir is None


def test_duplicate_mapping_block_ids_select_one_placement_once() -> None:
    class Block:
        id = "target"
        bbox = (0, 0, 100, 100)
        vector_coordinate_space = "page"
        vector_primitives = [{"kind": "rectangle", "bbox": (10, 10, 20, 20)}]

    observation = extract_vector_observation(
        Block(),
        (100, 100),
        max_primitives=1,
        max_texts=0,
        max_text_chars=0,
        source_mapping={
            "assembly": {
                "placements": [
                    {
                        "source_block_ids": ["target", "target"],
                        "page_bbox": [0, 0, 100, 100],
                        "page_to_canvas": [1, 0, 10, 0, 1, 10],
                    },
                    {
                        "source_block_ids": ["other"],
                        "page_bbox": [0, 0, 100, 100],
                        "page_to_canvas": [1, 0, 50, 0, 1, 50],
                    },
                ]
            }
        },
    )

    assert observation.primitives[0].bbox == (20.0, 20.0, 30.0, 30.0)
    assert not any("bbox fallback" in warning for warning in observation.warnings)


@pytest.mark.parametrize("source_id_count", [MAX_EVIDENCE_REFS, MAX_EVIDENCE_REFS + 1])
def test_mapping_source_id_limit_is_atomic(source_id_count: int) -> None:
    class Block:
        id = "target"
        bbox = (0, 0, 100, 100)
        vector_coordinate_space = "page"
        vector_primitives = [{"kind": "rectangle", "bbox": (10, 10, 20, 20)}]

    source_ids = ["target", *(f"id-{index}" for index in range(source_id_count - 1))]
    observation = extract_vector_observation(
        Block(),
        (100, 100),
        max_primitives=1,
        max_texts=0,
        max_text_chars=0,
        source_mapping={
            "assembly": {
                "placements": [
                    {
                        "source_block_ids": source_ids,
                        "page_bbox": [0, 0, 100, 100],
                        "page_to_canvas": [1, 0, 10, 0, 1, 10],
                    },
                    {
                        "source_block_ids": ["other"],
                        "page_bbox": [0, 0, 100, 100],
                        "page_to_canvas": [1, 0, 50, 0, 1, 50],
                    },
                ]
            }
        },
    )

    if source_id_count == MAX_EVIDENCE_REFS:
        assert observation.primitives[0].bbox == (20.0, 20.0, 30.0, 30.0)
        assert not any("bbox fallback" in warning for warning in observation.warnings)
    else:
        assert observation.primitives[0].bbox == (10.0, 10.0, 20.0, 20.0)
        assert any("bbox fallback" in warning for warning in observation.warnings)


def test_mapping_index_rejects_noncanonical_ids_before_hashing() -> None:
    hooks: list[str] = []

    class HashBomb(str):
        def __hash__(self) -> int:
            hooks.append("hash")
            raise AssertionError("string subclass hashing must not run")

    class Block:
        id = "target"
        bbox = (0, 0, 100, 100)
        vector_coordinate_space = "page"
        vector_primitives = [{"kind": "rectangle", "bbox": (10, 10, 20, 20)}]

    observation = extract_vector_observation(
        Block(),
        (100, 100),
        max_primitives=1,
        max_texts=0,
        max_text_chars=0,
        source_mapping={
            "assembly": {
                "placements": [
                    {
                        "source_block_ids": [
                            HashBomb("target"),
                            "x" * (MAX_VECTOR_TOKEN_CHARS + 1),
                            "\ud800",
                            "target",
                        ],
                        "page_bbox": [0, 0, 100, 100],
                        "page_to_canvas": [1, 0, 10, 0, 1, 10],
                    },
                    {
                        "source_block_ids": ["other"],
                        "page_bbox": [0, 0, 100, 100],
                        "page_to_canvas": [1, 0, 50, 0, 1, 50],
                    },
                ]
            }
        },
    )

    assert hooks == []
    assert observation.primitives[0].bbox == (20.0, 20.0, 30.0, 30.0)


def test_invalid_mapping_transform_still_contributes_to_ambiguity() -> None:
    class Block:
        id = object()
        bbox = (0, 0, 100, 100)
        vector_coordinate_space = "page"
        vector_primitives = [{"kind": "rectangle", "bbox": (10, 10, 20, 20)}]

    observation = extract_vector_observation(
        Block(),
        (100, 100),
        max_primitives=1,
        max_texts=0,
        max_text_chars=0,
        source_mapping={
            "assembly": {
                "placements": [
                    {
                        "source_block_ids": [],
                        "page_bbox": [0, 0, 100, 100],
                        "page_to_canvas": [1, 0],
                    },
                    {
                        "source_block_ids": [],
                        "page_bbox": [0, 0, 100, 100],
                        "page_to_canvas": [1, 0, 50, 0, 1, 50],
                    },
                ]
            }
        },
    )

    assert observation.primitives[0].bbox == (10.0, 10.0, 20.0, 20.0)
    assert any("bbox fallback" in warning for warning in observation.warnings)


def test_mapping_index_defers_transform_parsing_until_placement_is_selected() -> None:
    class BBoxBomb:
        @property
        def x0(self) -> float:
            raise RuntimeError("unused placement bbox must not be read")

        y0 = 0.0
        x1 = 100.0
        y1 = 100.0

    class Block:
        id = "target"
        page_id = 1
        bbox = (0, 0, 100, 100)
        vector_coordinate_space = "page"
        vector_primitives = [{"kind": "rectangle", "bbox": (10, 10, 20, 20)}]

    observation = extract_vector_observation(
        Block(),
        (100, 100),
        max_primitives=1,
        max_texts=0,
        max_text_chars=0,
        source_mapping={
            "assembly": {
                "placements": [
                    {
                        "page_id": 1,
                        "source_block_ids": ["target"],
                        "page_bbox": [0, 0, 100, 100],
                        "page_to_canvas": [1, 0, 10, 0, 1, 10],
                    },
                    {
                        "page_id": 2,
                        "source_block_ids": ["other"],
                        "page_bbox": BBoxBomb(),
                        "page_to_canvas": [1, 0, 50, 0, 1, 50],
                    },
                ]
            }
        },
    )

    assert observation.primitives[0].bbox == (20.0, 20.0, 30.0, 30.0)
    assert not any("bbox fallback" in warning for warning in observation.warnings)


def test_direct_and_engine_mapping_index_paths_are_equivalent() -> None:
    class Block:
        id = "target"
        page_id = 1
        bbox = (0, 0, 100, 100)
        vector_coordinate_space = "page"
        vector_primitives = [{"kind": "rectangle", "bbox": (10, 10, 20, 20)}]

    source = Block()
    source_mapping = {
        "assembly": {
            "placements": [
                {
                    "page_id": 1,
                    "source_block_ids": ["target"],
                    "page_bbox": [0, 0, 100, 100],
                    "page_to_canvas": [1, 0, 10, 0, 1, 10],
                }
            ]
        }
    }
    direct = extract_vector_observation(
        source,
        (200, 100),
        max_primitives=1,
        max_texts=0,
        max_text_chars=0,
        source_mapping=source_mapping,
    ).to_engine_observation(
        ["block-1"],
        max_primitives=1,
        max_texts=0,
        max_text_chars=0,
    )
    context = _context(source)
    context.vector_sources = [source]
    context.source_mapping = source_mapping

    indexed = VectorPrimitiveEngine(
        max_primitives=1,
        max_texts=0,
        max_text_chars=0,
    ).observe(context)

    assert indexed.model_dump(mode="json") == direct.model_dump(mode="json")


def test_vector_mapping_page_match_does_not_invoke_equality_hooks() -> None:
    hooks: list[str] = []

    class EqualityBomb:
        def __eq__(self, _other: object) -> bool:
            hooks.append("eq")
            raise AssertionError("custom equality must not run")

    class Block:
        id = object()
        page_id = 1
        bbox = (0, 0, 100, 100)
        vector_coordinate_space = "page"
        vector_primitives = [{"kind": "rectangle", "bbox": (10, 10, 20, 20)}]

    placements = [
        {
            "page_id": EqualityBomb(),
            "source_block_ids": [],
            "page_bbox": [0, 0, 100, 100],
            "page_to_canvas": [1, 0, 10, 0, 1, 10],
        },
        {
            "page_id": EqualityBomb(),
            "source_block_ids": [],
            "page_bbox": [0, 0, 100, 100],
            "page_to_canvas": [1, 0, 20, 0, 1, 20],
        },
    ]

    observation = extract_vector_observation(
        Block(),
        (100, 100),
        max_primitives=1,
        max_texts=0,
        max_text_chars=0,
        source_mapping={"assembly": {"placements": placements}},
    )

    assert hooks == []
    assert observation.primitives[0].bbox == (10.0, 10.0, 20.0, 20.0)
    assert any("bbox fallback" in warning for warning in observation.warnings)


def test_single_mismatched_page_placement_uses_bbox_fallback() -> None:
    class Block:
        id = object()
        page_id = 1
        bbox = (0, 0, 100, 100)
        vector_coordinate_space = "page"
        vector_primitives = [{"kind": "rectangle", "bbox": (10, 10, 20, 20)}]

    observation = extract_vector_observation(
        Block(),
        (100, 100),
        max_primitives=1,
        max_texts=0,
        max_text_chars=0,
        source_mapping={
            "assembly": {
                "placements": [
                    {
                        "page_id": 2,
                        "source_block_ids": [],
                        "page_bbox": [0, 0, 100, 100],
                        "page_to_canvas": [1, 0, 50, 0, 1, 50],
                    }
                ]
            }
        },
    )

    assert observation.primitives[0].bbox == (10.0, 10.0, 20.0, 20.0)
    assert any("bbox fallback" in warning for warning in observation.warnings)


def test_block_match_cannot_override_a_mismatched_source_page() -> None:
    class Block:
        id = "target"
        page_id = 1
        bbox = (0, 0, 100, 100)
        vector_coordinate_space = "page"
        vector_primitives = [{"kind": "rectangle", "bbox": (10, 10, 20, 20)}]

    observation = extract_vector_observation(
        Block(),
        (100, 100),
        max_primitives=1,
        max_texts=0,
        max_text_chars=0,
        source_mapping={
            "assembly": {
                "placements": [
                    {
                        "page_id": 2,
                        "source_block_ids": ["target"],
                        "page_bbox": [0, 0, 100, 100],
                        "page_to_canvas": [1, 0, 50, 0, 1, 50],
                    }
                ]
            }
        },
    )

    assert observation.primitives[0].bbox == (10.0, 10.0, 20.0, 20.0)
    assert any("bbox fallback" in warning for warning in observation.warnings)


def test_invalid_explicit_source_page_never_uses_a_unique_placement() -> None:
    huge = 10**5000
    placements = [
        {
            "page_id": 2,
            "source_block_ids": [],
            "page_bbox": [0, 0, 100, 100],
            "page_to_canvas": [1, 0, 50, 0, 1, 50],
        }
    ]

    for page_id in ("1", True, object(), huge):
        source = type(
            "Source",
            (),
            {
                "id": object(),
                "page_id": page_id,
                "bbox": (0, 0, 100, 100),
                "vector_coordinate_space": "page",
                "vector_primitives": [{"kind": "rectangle", "bbox": (10, 10, 20, 20)}],
            },
        )()

        observation = extract_vector_observation(
            source,
            (100, 100),
            max_primitives=1,
            max_texts=0,
            max_text_chars=0,
            source_mapping={"assembly": {"placements": placements}},
        )

        assert observation.primitives[0].bbox == (10.0, 10.0, 20.0, 20.0)
        assert any("bbox fallback" in warning for warning in observation.warnings)


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


def test_vector_primitive_generator_consumes_only_the_budget_and_lookahead() -> None:
    consumed: list[int] = []

    def primitives():
        for index in range(100):
            consumed.append(index)
            yield {
                "kind": "rectangle",
                "bbox": (index * 10, 0, index * 10 + 5, 5),
            }

    class Provider:
        vector_primitives = primitives()

    observation = extract_vector_observation(
        Provider(),
        (200, 100),
        max_primitives=2,
        max_texts=0,
        max_text_chars=0,
    )

    assert consumed == [0, 1, 2]
    assert len(observation.primitives) == 2
    assert any("primitive input budget" in warning for warning in observation.warnings)


def test_vector_text_generator_stops_at_the_count_and_character_budgets() -> None:
    consumed: list[int] = []

    def texts():
        for index, text in enumerate(("abcd", "efgh", "ignored")):
            consumed.append(index)
            yield {"text": text, "bbox": (0, index * 10, 20, index * 10 + 5)}

    class Provider:
        vector_texts = texts()

    observation = extract_vector_observation(
        Provider(),
        (100, 100),
        max_primitives=0,
        max_texts=3,
        max_text_chars=5,
    )

    assert consumed == [0, 1]
    assert [item.text for item in observation.texts] == ["abcd"]
    assert any("text character budget" in warning for warning in observation.warnings)


def test_duck_typed_vector_text_consumes_the_character_budget_before_parsing() -> None:
    @dataclass
    class Span:
        text: str
        bbox: tuple[int, int, int, int]

    class Provider:
        vector_texts = [Span("too-long", (0, 0, 20, 10))]

    observation = extract_vector_observation(
        Provider(),
        (100, 100),
        max_primitives=0,
        max_texts=1,
        max_text_chars=4,
    )

    assert observation.texts == ()
    assert observation.text_chars_seen == 4
    assert observation.text_char_budget_exhausted is True
    assert any("text character budget" in warning for warning in observation.warnings)


def test_duck_typed_vector_text_is_snapshotted_before_character_accounting() -> None:
    class Span:
        bbox = (0, 0, 20, 10)

        def __init__(self) -> None:
            self.calls = 0

        @property
        def text(self) -> str:
            self.calls += 1
            return "a" if self.calls == 1 else "x" * MAX_TEXT_CHARS

    span = Span()

    class Provider:
        vector_texts = [span]

    observation = extract_vector_observation(
        Provider(),
        (100, 100),
        max_primitives=0,
        max_texts=1,
        max_text_chars=1,
    )

    assert span.calls == 1
    assert [item.text for item in observation.texts] == ["a"]
    assert observation.text_chars_seen == 1
    assert not any("text character budget" in warning for warning in observation.warnings)


@pytest.mark.parametrize("source_kind", ["dict", "words"])
def test_get_text_records_are_snapshotted_before_character_accounting(
    source_kind: str,
) -> None:
    class Span:
        bbox = (0, 0, 20, 10)

        def __init__(self) -> None:
            self.calls = 0

        @property
        def text(self) -> str:
            self.calls += 1
            return "a" if self.calls == 1 else "x" * MAX_TEXT_CHARS

    span = Span()

    class Provider:
        def get_text(self, mode: str):
            if mode == "dict":
                if source_kind == "dict":
                    return {"blocks": [{"lines": [{"spans": [span]}]}]}
                return {"blocks": []}
            return [span] if source_kind == "words" else []

    observation = extract_vector_observation(
        Provider(),
        (100, 100),
        max_primitives=0,
        max_texts=1,
        max_text_chars=1,
    )

    assert span.calls == 1
    assert [item.text for item in observation.texts] == ["a"]
    assert observation.text_chars_seen == 1
    assert not any("text character budget" in warning for warning in observation.warnings)


def test_nested_drawing_items_consume_only_the_primitive_budget_and_lookahead() -> None:
    consumed: list[int] = []

    def items():
        for index in range(100):
            consumed.append(index)
            yield ("re", (index * 10, 0, index * 10 + 5, 5))

    class Provider:
        vector_primitives = [{"items": items()}]

    observation = extract_vector_observation(
        Provider(),
        (200, 100),
        max_primitives=2,
        max_texts=0,
        max_text_chars=0,
    )

    assert consumed == [0, 1, 2]
    assert len(observation.primitives) == 2
    assert any("primitive input budget" in warning for warning in observation.warnings)


def test_nested_command_budget_is_applied_before_materializing_outer_drawings() -> None:
    outer_seen: list[int] = []
    inner_seen: list[tuple[int, int]] = []

    def items(outer_index: int):
        for item_index in range(100):
            inner_seen.append((outer_index, item_index))
            yield ("re", (item_index * 10, 0, item_index * 10 + 5, 5))

    def drawings():
        for outer_index in range(100):
            outer_seen.append(outer_index)
            yield {"items": items(outer_index)}

    class Provider:
        vector_primitives = drawings()

    observation = extract_vector_observation(
        Provider(),
        (200, 100),
        max_primitives=2,
        max_texts=0,
        max_text_chars=0,
    )

    assert outer_seen == [0]
    assert inner_seen == [(0, 0), (0, 1), (0, 2)]
    assert len(observation.primitives) == 2
    assert observation.primitive_records_seen == 2
    assert observation.primitive_budget_exhausted is True


def test_vector_polygon_point_generator_is_bounded_without_partial_geometry() -> None:
    consumed: list[int] = []

    def points():
        for index in range(MAX_VECTOR_POLYGON_POINTS + 10):
            consumed.append(index)
            yield (index, index)

    class Provider:
        vector_primitives = [
            {
                "kind": "polygon",
                "bbox": (
                    0,
                    0,
                    MAX_VECTOR_POLYGON_POINTS,
                    MAX_VECTOR_POLYGON_POINTS,
                ),
                "points": points(),
            }
        ]

    observation = extract_vector_observation(
        Provider(),
        (100, 100),
        max_primitives=1,
        max_texts=0,
        max_text_chars=0,
    )

    assert consumed == list(range(MAX_VECTOR_POLYGON_POINTS + 1))
    assert observation.primitives == ()
    assert any("malformed vector record" in warning for warning in observation.warnings)


def test_vector_point_limits_match_the_documented_resource_contract() -> None:
    assert MAX_VECTOR_POLYGON_POINTS == 256
    assert MAX_VECTOR_POLYLINE_POINTS == 512
    assert MAX_VECTOR_TOTAL_POINTS == 100_000
    assert MAX_VECTOR_TOKEN_CHARS == 256


def test_vector_aggregate_point_budget_omits_whole_overflow_geometry() -> None:
    points = ((0, 0), (10, 0), (10, 10), (0, 10))
    observation = VectorObservation(
        canvas_size=(100, 100),
        primitives=(
            VectorPrimitive(
                kind="polygon",
                bbox=(0, 0, 10, 10),
                points=points,
                closed=True,
            ),
            VectorPrimitive(
                kind="polygon",
                bbox=(20, 0, 30, 10),
                points=points,
                closed=True,
            ),
            VectorPrimitive(
                kind="rectangle",
                bbox=(40, 0, 50, 10),
                closed=True,
            ),
        ),
    )

    result = observation.to_engine_observation(
        ["source"],
        max_primitives=3,
        max_texts=0,
        max_text_chars=0,
        max_points=4,
    )

    assert result.scene_ir is not None
    assert [element.bbox for element in result.scene_ir.elements] == [
        (0.0, 0.0, 10.0, 10.0),
        (40.0, 0.0, 50.0, 10.0),
    ]
    assert any("aggregate point budget" in warning for warning in result.warnings)


def test_exhausted_point_budget_still_allows_later_point_free_primitives() -> None:
    class First:
        vector_primitives = [
            {
                "kind": "polygon",
                "bbox": (0, 0, 10, 10),
                "points": ((0, 0), (10, 0), (10, 10), (0, 10)),
            }
        ]

    class Second:
        vector_primitives = [
            {
                "kind": "rectangle",
                "bbox": (20, 0, 30, 10),
            }
        ]

    context = _context("fallback")
    context.vector_sources = [First(), Second()]  # type: ignore[attr-defined]

    result = VectorPrimitiveEngine(
        max_primitives=2,
        max_texts=0,
        max_text_chars=0,
        max_points=4,
    ).observe(context)

    assert result.scene_ir is not None
    assert len(result.scene_ir.elements) == 2


def test_vector_engine_stops_before_extracting_sources_beyond_the_global_budget() -> None:
    context = _context("fallback")
    context.vector_sources = ["first", "second", "third"]  # type: ignore[attr-defined]
    seen: list[str] = []

    def extractor(source: str, _size: tuple[int, int]) -> VectorObservation:
        seen.append(source)
        index = len(seen)
        return VectorObservation(
            canvas_size=(100, 100),
            texts=(VectorText(f"Node {index}", (index * 20, 5, index * 20 + 10, 10)),),
            primitives=(
                VectorPrimitive(
                    kind="rectangle",
                    bbox=(index * 20, 0, index * 20 + 15, 15),
                    closed=True,
                ),
            ),
        )

    result = VectorPrimitiveEngine(
        extractor=extractor,
        max_primitives=2,
        max_texts=2,
        max_text_chars=100,
    ).observe(context)

    assert seen == ["first", "second"]
    assert result.scene_ir is not None
    assert len(result.scene_ir.elements) == 2
    assert len(result.evidence) == 4
    assert any("source collection" in warning for warning in result.warnings)


def test_malformed_records_still_consume_the_reconstruction_global_work_budget() -> None:
    consumed = {source_index: [] for source_index in range(3)}

    def records(source_index: int):
        for record_index in range(10):
            consumed[source_index].append(record_index)
            yield {"unsupported": record_index}

    class Provider:
        def __init__(self, source_index: int):
            self.vector_primitives = records(source_index)

    context = _context("fallback")
    context.vector_sources = [Provider(index) for index in range(3)]  # type: ignore[attr-defined]

    result = VectorPrimitiveEngine(
        max_primitives=2,
        max_texts=0,
        max_text_chars=0,
    ).observe(context)

    assert consumed == {0: [0, 1, 2], 1: [], 2: []}
    assert result.scene_ir is None
    assert any("source collection" in warning for warning in result.warnings)


def test_duplicate_records_still_consume_the_reconstruction_global_work_budget() -> None:
    consumed = {source_index: [] for source_index in range(2)}

    def records(source_index: int):
        for record_index in range(2):
            consumed[source_index].append(record_index)
            yield {
                "kind": "rectangle",
                "bbox": (0, 0, 20, 20),
                "closed": True,
            }

    class Provider:
        def __init__(self, source_index: int):
            self.vector_primitives = records(source_index)

    context = _context("fallback")
    context.vector_sources = [Provider(index) for index in range(2)]  # type: ignore[attr-defined]

    result = VectorPrimitiveEngine(
        max_primitives=2,
        max_texts=0,
        max_text_chars=0,
    ).observe(context)

    assert consumed == {0: [0, 1], 1: []}
    assert result.scene_ir is not None
    assert len(result.scene_ir.elements) == 1
    assert any("source collection" in warning for warning in result.warnings)


def test_cropped_out_records_still_consume_the_reconstruction_global_work_budget() -> None:
    consumed = {source_index: [] for source_index in range(2)}

    def records(source_index: int):
        for record_index in range(2):
            consumed[source_index].append(record_index)
            yield {
                "kind": "rectangle",
                "bbox": (60, 60, 80, 80),
                "closed": True,
            }

    class Provider:
        vector_coordinate_space = "page"
        bbox = (0, 0, 50, 50)

        def __init__(self, source_index: int):
            self.vector_primitives = records(source_index)

    context = _context("fallback")
    context.vector_sources = [Provider(index) for index in range(2)]  # type: ignore[attr-defined]

    result = VectorPrimitiveEngine(
        max_primitives=2,
        max_texts=0,
        max_text_chars=0,
    ).observe(context)

    assert consumed == {0: [0, 1], 1: []}
    assert result.scene_ir is None
    assert any("source collection" in warning for warning in result.warnings)


def test_empty_nested_drawing_containers_consume_global_work_budget() -> None:
    consumed = {source_index: [] for source_index in range(2)}

    def records(source_index: int):
        for record_index in range(10):
            consumed[source_index].append(record_index)
            yield {"items": []}

    class Provider:
        def __init__(self, source_index: int):
            self.vector_primitives = records(source_index)

    context = _context("fallback")
    context.vector_sources = [Provider(index) for index in range(2)]  # type: ignore[attr-defined]

    result = VectorPrimitiveEngine(
        max_primitives=2,
        max_texts=0,
        max_text_chars=0,
    ).observe(context)

    assert consumed == {0: [0, 1, 2], 1: []}
    assert result.scene_ir is None
    assert any("source collection" in warning for warning in result.warnings)


def test_text_character_overflow_closes_later_vector_sources() -> None:
    context = _context("fallback")
    context.vector_sources = ["first", "second"]  # type: ignore[attr-defined]
    seen: list[str] = []

    def extractor(source: str, size: tuple[int, int]) -> VectorObservation:
        seen.append(source)
        return VectorObservation(
            canvas_size=size,
            texts=(VectorText("four", (0, 0, 10, 10)),),
            primitives=(VectorPrimitive(kind="rectangle", bbox=(0, 0, 20, 20), closed=True),),
        )

    result = VectorPrimitiveEngine(
        extractor=extractor,
        max_primitives=1,
        max_texts=2,
        max_text_chars=3,
    ).observe(context)

    assert seen == ["first"]
    assert result.scene_ir is not None
    assert result.scene_ir.elements[0].text is None
    assert any("text character budget" in warning for warning in result.warnings)
    assert any("source collection" in warning for warning in result.warnings)


def test_closed_primitive_dimension_is_not_probed_while_later_text_is_collected() -> None:
    later_primitives_seen: list[int] = []

    def later_primitives():
        for index in range(10):
            later_primitives_seen.append(index)
            yield {"kind": "rectangle", "bbox": (20, 0, 30, 10)}

    class First:
        vector_primitives = [{"kind": "rectangle", "bbox": (0, 0, 10, 10)}]

    class Second:
        vector_primitives = later_primitives()
        vector_texts = [{"text": "later", "bbox": (0, 0, 10, 10)}]

    context = _context("fallback")
    context.vector_sources = [First(), Second()]  # type: ignore[attr-defined]

    result = VectorPrimitiveEngine(
        max_primitives=1,
        max_texts=2,
        max_text_chars=100,
    ).observe(context)

    assert later_primitives_seen == []
    assert any(item.text == "later" for item in result.evidence)


def test_closed_text_dimension_is_not_probed_while_later_primitives_are_collected() -> None:
    later_texts_seen: list[int] = []

    def later_texts():
        for index in range(10):
            later_texts_seen.append(index)
            yield {"text": "ignored", "bbox": (0, 0, 10, 10)}

    class First:
        vector_texts = [{"text": "first", "bbox": (0, 0, 10, 10)}]

    class Second:
        vector_texts = later_texts()
        vector_primitives = [{"kind": "rectangle", "bbox": (0, 0, 10, 10)}]

    context = _context("fallback")
    context.vector_sources = [First(), Second()]  # type: ignore[attr-defined]

    result = VectorPrimitiveEngine(
        max_primitives=2,
        max_texts=1,
        max_text_chars=100,
    ).observe(context)

    assert later_texts_seen == []
    assert result.scene_ir is not None
    assert len(result.scene_ir.elements) == 1


def test_custom_observation_does_not_iterate_a_closed_primitive_dimension() -> None:
    primitive_seen: list[int] = []

    def primitives():
        for index in range(10):
            primitive_seen.append(index)
            yield VectorPrimitive(kind="rectangle", bbox=(20, 0, 30, 10), closed=True)

    observations = {
        "first": VectorObservation(
            canvas_size=(200, 100),
            primitives=(VectorPrimitive(kind="rectangle", bbox=(0, 0, 10, 10), closed=True),),
        ),
        "second": VectorObservation(
            canvas_size=(200, 100),
            primitives=primitives(),  # type: ignore[arg-type]
            texts=(VectorText("later", (0, 0, 10, 10)),),
        ),
    }
    context = _context("fallback")
    context.vector_sources = ["first", "second"]  # type: ignore[attr-defined]

    result = VectorPrimitiveEngine(
        extractor=lambda source, _size: observations[source],
        max_primitives=1,
        max_texts=2,
        max_text_chars=100,
    ).observe(context)

    assert primitive_seen == []
    assert any(item.text == "later" for item in result.evidence)


def test_custom_observation_does_not_iterate_a_closed_text_dimension() -> None:
    text_seen: list[int] = []

    def texts():
        for index in range(10):
            text_seen.append(index)
            yield VectorText("ignored", (0, 0, 10, 10))

    observations = {
        "first": VectorObservation(
            canvas_size=(200, 100),
            texts=(VectorText("first", (0, 0, 10, 10)),),
        ),
        "second": VectorObservation(
            canvas_size=(200, 100),
            texts=texts(),  # type: ignore[arg-type]
            primitives=(VectorPrimitive(kind="rectangle", bbox=(0, 0, 10, 10), closed=True),),
        ),
    }
    context = _context("fallback")
    context.vector_sources = ["first", "second"]  # type: ignore[attr-defined]

    result = VectorPrimitiveEngine(
        extractor=lambda source, _size: observations[source],
        max_primitives=2,
        max_texts=1,
        max_text_chars=100,
    ).observe(context)

    assert text_seen == []
    assert result.scene_ir is not None
    assert len(result.scene_ir.elements) == 1


def test_invalid_dict_text_records_are_bounded_before_filtering() -> None:
    consumed: list[int] = []

    def blocks():
        for index in range(100):
            consumed.append(index)
            yield None

    class Provider:
        def get_text(self, mode: str):
            if mode == "dict":
                return {"blocks": blocks()}
            return []

    observation = extract_vector_observation(
        Provider(),
        (100, 100),
        max_primitives=0,
        max_texts=2,
        max_text_chars=100,
    )

    assert consumed == [0, 1, 2]
    assert observation.texts == ()
    assert observation.text_records_seen == 2
    assert observation.text_count_budget_exhausted is True
    assert any("malformed vector record" in warning for warning in observation.warnings)
    assert any("text count budget" in warning for warning in observation.warnings)


def test_invalid_word_records_are_bounded_before_filtering() -> None:
    consumed: list[int] = []

    def words():
        for index in range(100):
            consumed.append(index)
            yield object()

    class Provider:
        def get_text(self, mode: str):
            if mode == "dict":
                return {"blocks": []}
            return words()

    observation = extract_vector_observation(
        Provider(),
        (100, 100),
        max_primitives=0,
        max_texts=2,
        max_text_chars=100,
    )

    assert consumed == [0, 1, 2]
    assert observation.texts == ()
    assert observation.text_records_seen == 2
    assert observation.text_count_budget_exhausted is True
    assert any("malformed vector record" in warning for warning in observation.warnings)


def test_vector_engine_bounds_direct_source_collection_materialization() -> None:
    generated: list[int] = []
    context = _context("fallback")

    def sources():
        for index in range(MAX_EVIDENCE_REFS + 20):
            generated.append(index)
            yield index

    context.vector_sources = sources()  # type: ignore[attr-defined]
    seen: list[int] = []

    def extractor(source: int, _size: tuple[int, int]) -> VectorObservation:
        seen.append(source)
        return VectorObservation(canvas_size=(100, 100))

    result = VectorPrimitiveEngine(extractor=extractor).observe(context)

    assert generated == list(range(MAX_EVIDENCE_REFS + 1))
    assert seen == list(range(MAX_EVIDENCE_REFS))
    assert any("source count budget" in warning for warning in result.warnings)


def test_exact_vector_source_limit_does_not_report_source_count_truncation() -> None:
    context = _context("fallback")
    context.vector_sources = list(range(MAX_EVIDENCE_REFS))  # type: ignore[attr-defined]

    result = VectorPrimitiveEngine(
        extractor=lambda _source, size: VectorObservation(canvas_size=size),
        max_primitives=1,
        max_texts=1,
        max_text_chars=1,
    ).observe(context)

    assert not any("source count budget" in warning for warning in result.warnings)


def test_exact_vector_record_limits_do_not_report_input_truncation() -> None:
    class Provider:
        vector_texts = [
            {"text": "a", "bbox": (0, 0, 10, 10)},
            {"text": "b", "bbox": (20, 0, 30, 10)},
        ]
        vector_primitives = [
            {"kind": "rectangle", "bbox": (0, 0, 10, 10)},
            {"kind": "rectangle", "bbox": (20, 0, 30, 10)},
        ]

    observation = extract_vector_observation(
        Provider(),
        (100, 100),
        max_primitives=2,
        max_texts=2,
        max_text_chars=2,
    )

    assert len(observation.primitives) == 2
    assert len(observation.texts) == 2
    assert not any("input budget" in warning for warning in observation.warnings)
    assert not any("text count budget" in warning for warning in observation.warnings)
    assert not any("text character budget" in warning for warning in observation.warnings)


def test_direct_vector_observation_reapplies_global_record_budgets() -> None:
    observation = VectorObservation(
        canvas_size=(100, 100),
        texts=tuple(
            VectorText(f"Node {index}", (0, index * 10, 10, index * 10 + 5)) for index in range(3)
        ),
        primitives=tuple(
            VectorPrimitive(
                kind="rectangle",
                bbox=(index * 20, 0, index * 20 + 15, 15),
                closed=True,
            )
            for index in range(3)
        ),
    )

    result = observation.to_engine_observation(
        ["source"],
        max_primitives=2,
        max_texts=2,
        max_text_chars=100,
    )

    assert result.scene_ir is not None
    assert len(result.scene_ir.elements) == 2
    assert len(result.evidence) == 4
    assert any("primitive input budget" in warning for warning in result.warnings)
    assert any("text count budget" in warning for warning in result.warnings)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("fill_color", "x" * (MAX_TEXT_CHARS + 1)),
        ("stroke_color", "\ud800"),
        ("line_style", object()),
    ],
)
def test_invalid_primitive_styles_omit_the_whole_record(
    field: str,
    value: object,
) -> None:
    primitive = VectorPrimitive(kind="rectangle", bbox=(0, 0, 20, 20), closed=True)
    object.__setattr__(primitive, field, value)
    observation = VectorObservation(canvas_size=(100, 100), primitives=(primitive,))

    result = observation.to_engine_observation(["source"])

    assert result.scene_ir is None
    assert result.evidence == []
    assert any("invalid vector primitive" in warning for warning in result.warnings)
    assert type(result).model_validate(result.model_dump()) == result


def test_raw_oversized_primitive_style_is_omitted_without_failing_extraction() -> None:
    class Provider:
        vector_primitives = [
            {
                "kind": "rectangle",
                "bbox": (0, 0, 20, 20),
                "fill_color": "x" * (MAX_TEXT_CHARS + 1),
                "closed": True,
            }
        ]

    observation = extract_vector_observation(
        Provider(),
        (100, 100),
        max_primitives=1,
        max_texts=0,
        max_text_chars=0,
    )

    assert observation.primitives == ()
    assert any("malformed vector record" in warning for warning in observation.warnings)


@pytest.mark.parametrize("token_size", [MAX_VECTOR_TOKEN_CHARS, MAX_VECTOR_TOKEN_CHARS + 1])
def test_vector_metadata_token_limit_is_exact(token_size: int) -> None:
    token = "x" * token_size

    class Provider:
        vector_primitives = [
            {
                "kind": "rectangle",
                "bbox": (0, 0, 20, 20),
                "fill_color": token,
            }
        ]

    observation = extract_vector_observation(
        Provider(),
        (100, 100),
        max_primitives=1,
        max_texts=0,
        max_text_chars=0,
    )

    if token_size == MAX_VECTOR_TOKEN_CHARS:
        assert len(observation.primitives) == 1
        assert observation.primitives[0].fill_color == token
        assert not any("malformed vector record" in warning for warning in observation.warnings)
    else:
        assert observation.primitives == ()
        assert any("malformed vector record" in warning for warning in observation.warnings)


def test_vector_scalar_normalization_does_not_invoke_string_coercion_hooks() -> None:
    hooks: list[str] = []

    class StringBomb:
        def __str__(self) -> str:
            hooks.append("str")
            raise AssertionError("string coercion must not run")

    class HookString(str):
        def strip(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            hooks.append("strip")
            raise AssertionError("string subclass hooks must not run")

        def lower(self):
            hooks.append("lower")
            raise AssertionError("string subclass hooks must not run")

        def casefold(self):
            hooks.append("casefold")
            raise AssertionError("string subclass hooks must not run")

    class Provider:
        vector_coordinate_space = HookString("page")
        vector_primitives = [
            {"kind": StringBomb(), "bbox": (0, 0, 10, 10)},
            {"items": [(StringBomb(), (0, 0), (10, 10))]},
            {
                "kind": "rectangle",
                "bbox": (0, 0, 10, 10),
                "fill_color": HookString("#ffffff"),
            },
        ]

    observation = extract_vector_observation(
        Provider(),
        (100, 100),
        max_primitives=3,
        max_texts=0,
        max_text_chars=0,
    )

    assert hooks == []
    assert observation.primitives == ()
    assert any("malformed vector record" in warning for warning in observation.warnings)


def test_vector_source_identifiers_reject_huge_integers_without_decimal_conversion() -> None:
    huge = 10**5000

    class BlockType:
        name = "Figure"

    marker_page_id = type(
        "BlockId",
        (),
        {
            "__module__": "marker.schema.blocks.base",
            "page_id": huge,
            "block_id": None,
            "block_type": None,
        },
    )()
    marker_block_id = type(
        "BlockId",
        (),
        {
            "__module__": "marker.schema.blocks.base",
            "page_id": 1,
            "block_id": huge,
            "block_type": BlockType(),
        },
    )()
    placements = [
        {
            "page_id": index,
            "source_block_ids": [f"other-{index}"],
            "page_bbox": [0, 0, 100, 100],
            "page_to_canvas": [1, 0, index, 0, 1, index],
        }
        for index in range(2)
    ]

    for identifier in (huge, marker_page_id, marker_block_id):
        source = type(
            "Source",
            (),
            {
                "id": identifier,
                "page_id": huge,
                "bbox": (0, 0, 100, 100),
                "vector_coordinate_space": "page",
                "vector_primitives": [{"kind": "rectangle", "bbox": (0, 0, 10, 10)}],
            },
        )()

        observation = extract_vector_observation(
            source,
            (100, 100),
            max_primitives=1,
            max_texts=0,
            max_text_chars=0,
            source_mapping={"assembly": {"placements": placements}},
        )

        assert len(observation.primitives) == 1
        assert any("bbox fallback" in warning for warning in observation.warnings)


def test_vector_numeric_normalization_isolates_huge_integer_coordinates() -> None:
    huge = 10**10000

    class Provider:
        vector_texts = [{"text": "Node", "bbox": (0, 0, huge, 10)}]
        vector_primitives = [
            {"kind": "rectangle", "bbox": (0, 0, huge, 10)},
        ]

    observation = extract_vector_observation(
        Provider(),
        (100, 100),
        max_primitives=1,
        max_texts=1,
        max_text_chars=10,
    )

    assert observation.texts == ()
    assert observation.primitives == ()
    assert any("malformed vector record" in warning for warning in observation.warnings)

    direct = VectorObservation(
        canvas_size=(100, 100),
        texts=(VectorText("Node", (0, 0, huge, 10), confidence=huge),),
        primitives=(
            VectorPrimitive(
                kind="line",
                bbox=(0, 0, huge, 10),
                points=((0, 0), (huge, 10)),
                confidence=huge,
            ),
        ),
    ).to_engine_observation(["source"], max_primitives=1, max_texts=1)

    assert direct.scene_ir is None
    assert direct.evidence == []
    assert any("primitive records were omitted" in warning for warning in direct.warnings)
    assert any("vector text records were omitted" in warning for warning in direct.warnings)


def test_vector_affine_normalization_rejects_custom_sequences_without_iteration() -> None:
    calls: list[str] = []

    class TrapSequence(Sequence[float]):
        def __len__(self) -> int:
            calls.append("len")
            raise AssertionError("custom sequence length must not run")

        def __getitem__(self, index: int) -> float:
            calls.append(f"get-{index}")
            raise AssertionError("custom sequence indexing must not run")

    class Block:
        id = "block"
        page_id = 1
        bbox = (0, 0, 100, 100)
        vector_coordinate_space = "page"
        vector_primitives = [
            {"kind": "rectangle", "bbox": (0, 0, 10, 10)},
        ]

    observation = extract_vector_observation(
        Block(),
        (100, 100),
        max_primitives=1,
        max_texts=0,
        max_text_chars=0,
        source_mapping={
            "assembly": {
                "placements": [
                    {
                        "page_id": 1,
                        "source_block_ids": ["block"],
                        "page_bbox": [0, 0, 100, 100],
                        "page_to_canvas": TrapSequence(),
                    }
                ]
            }
        },
    )

    assert calls == []
    assert len(observation.primitives) == 1
    assert any("bbox fallback" in warning for warning in observation.warnings)


def test_malformed_custom_vector_text_is_omitted_without_crashing_engine() -> None:
    malformed = VectorText("valid", (0, 0, 10, 10))
    object.__setattr__(malformed, "text", object())
    engine = VectorPrimitiveEngine(
        extractor=lambda _source, size: VectorObservation(
            canvas_size=size,
            texts=(malformed,),
        ),
        max_primitives=1,
        max_texts=1,
        max_text_chars=10,
    )

    result = engine.observe(_context("source"))

    assert result.scene_ir is None
    assert result.evidence == []
    assert any("invalid or oversized vector text" in warning for warning in result.warnings)
    assert type(result).model_validate(result.model_dump()) == result


def test_custom_vector_text_font_weight_does_not_invoke_hash_hooks() -> None:
    hooks: list[str] = []

    class HashBomb:
        def __hash__(self) -> int:
            hooks.append("hash")
            raise AssertionError("font-weight hash hook must not run")

    malformed = VectorText("valid", (0, 0, 10, 10))
    object.__setattr__(malformed, "font_weight", HashBomb())
    result = VectorPrimitiveEngine(
        extractor=lambda _source, size: VectorObservation(
            canvas_size=size,
            texts=(malformed,),
        ),
        max_primitives=1,
        max_texts=1,
        max_text_chars=10,
    ).observe(_context("source"))

    assert hooks == []
    assert result.evidence == []
    assert any("invalid or oversized vector text" in warning for warning in result.warnings)


def test_direct_vector_observation_omits_non_iterable_collections() -> None:
    observation = VectorObservation(canvas_size=(100, 100))
    object.__setattr__(observation, "primitives", object())
    object.__setattr__(observation, "texts", object())
    object.__setattr__(observation, "warnings", object())

    result = observation.to_engine_observation(object())  # type: ignore[arg-type]

    assert result.scene_ir is None
    assert result.evidence == []
    assert any("source-block collection" in warning for warning in result.warnings)
    assert any("primitive collection" in warning for warning in result.warnings)
    assert any("text collection" in warning for warning in result.warnings)
    assert any("warning collection" in warning for warning in result.warnings)


def test_direct_vector_observation_isolates_invalid_canvas_and_tolerance() -> None:
    observation = VectorObservation(
        canvas_size=(100, 100),
        primitives=(VectorPrimitive(kind="rectangle", bbox=(0, 0, 20, 20), closed=True),),
    )
    object.__setattr__(observation, "canvas_size", object())

    invalid_canvas = observation.to_engine_observation(["source"])

    assert invalid_canvas.scene_ir is None
    assert invalid_canvas.evidence == []
    assert any("canvas size" in warning for warning in invalid_canvas.warnings)

    valid_observation = VectorObservation(canvas_size=(100, 100))
    invalid_tolerance = valid_observation.to_engine_observation(
        ["source"],
        endpoint_tolerance=float("nan"),
    )

    assert invalid_tolerance.scene_ir is None
    assert any("endpoint tolerance" in warning for warning in invalid_tolerance.warnings)

    huge = 10**5000
    huge_canvas = VectorObservation(canvas_size=(huge, 100)).to_engine_observation(["source"])
    huge_tolerance = valid_observation.to_engine_observation(
        ["source"],
        endpoint_tolerance=huge,
    )

    assert huge_canvas.scene_ir is None
    assert any("canvas size" in warning for warning in huge_canvas.warnings)
    assert huge_tolerance.scene_ir is None
    assert any("endpoint tolerance" in warning for warning in huge_tolerance.warnings)
    with pytest.raises(ValueError, match="endpoint_tolerance"):
        VectorPrimitiveEngine(endpoint_tolerance=huge)


@pytest.mark.parametrize("block_count", [MAX_EVIDENCE_REFS, MAX_EVIDENCE_REFS + 1])
def test_source_block_provenance_is_preserved_or_omitted_atomically(
    block_count: int,
) -> None:
    block_ids = [f"block-{index}" for index in range(block_count)]
    observation = VectorObservation(
        canvas_size=(100, 100),
        primitives=(VectorPrimitive(kind="rectangle", bbox=(0, 0, 20, 20), closed=True),),
    )

    result = observation.to_engine_observation(block_ids)

    assert result.evidence
    if block_count == MAX_EVIDENCE_REFS:
        assert result.evidence[0].source_block_ids == block_ids
        assert not any("source-block provenance" in warning for warning in result.warnings)
    else:
        assert result.evidence[0].source_block_ids == []
        assert any("source-block provenance" in warning for warning in result.warnings)


def test_vector_warning_collection_is_canonicalized_at_the_observation_boundary() -> None:
    observation = VectorObservation(
        canvas_size=(100, 100),
        warnings=tuple(f"source warning {index}" for index in range(MAX_OBSERVATION_WARNINGS + 10)),
    )

    result = observation.to_engine_observation(["source"])

    assert len(result.warnings) == MAX_OBSERVATION_WARNINGS
    assert result.warnings[-1] == "vector warnings were truncated to the observation budget"


def test_vector_engine_rejects_impossible_global_budget_configuration() -> None:
    with pytest.raises(ValueError, match="scene element limit"):
        VectorPrimitiveEngine(max_primitives=MAX_SCENE_ELEMENTS + 1)
    with pytest.raises(ValueError, match="observation evidence limit"):
        VectorPrimitiveEngine(
            max_primitives=MAX_SCENE_ELEMENTS,
            max_texts=MAX_OBSERVATION_EVIDENCE - MAX_SCENE_ELEMENTS + 1,
        )
    with pytest.raises(ValueError, match="vector point limit"):
        VectorPrimitiveEngine(max_points=MAX_VECTOR_TOTAL_POINTS + 1)
