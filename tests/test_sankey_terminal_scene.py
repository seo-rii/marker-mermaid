from __future__ import annotations

from collections import Counter
from decimal import Decimal
from xml.etree import ElementTree as ET

import pytest
from PIL import Image

from marker_mermaid.candidate_scene import typed_ir_semantic_texts, typed_ir_to_scene
from marker_mermaid.config import MermaidConfig
from marker_mermaid.engines import JsonFixtureEngine
from marker_mermaid.models import (
    MAX_ID_CHARS,
    MAX_SCENE_RELATIONS,
    DiagramTypePrediction,
    EngineObservation,
    TypedIRCandidate,
    VisualEvidence,
)
from marker_mermaid.pipeline import ReconstructionPipeline
from marker_mermaid.protocols import RuntimeResult
from marker_mermaid.resource_limits import MAX_EVIDENCE_REFS
from marker_mermaid.serializers import SerializationError
from marker_mermaid.serializers_charts_flow import (
    MAX_SANKEY_FLOWCHART_EDGES,
    serialize_sankey,
)
from marker_mermaid.validation import CandidateValidator, NodeMermaidRuntime

NATIVE_SANKEY_IR = {
    "title": "Hidden native heading",
    "description": "Hidden native description",
    "direction": "TB",
    "nodes": [
        {
            "id": "a",
            "label": " A ",
            "text": "Hidden A alias",
            "role": "hidden-role",
            "shape": "diamond",
            "bbox": [1, 2, 11, 12],
            "evidence_ids": ["ocr-a"],
        },
        {"id": "b", "label": "B", "evidence_ids": ["ocr-b"]},
        {"id": "m", "label": "M", "evidence_ids": ["ocr-m"]},
        {"id": "t", "label": "T", "evidence_ids": ["ocr-t"]},
    ],
    "flows": [
        {
            "id": "flow-a-m",
            "source": "a",
            "target": "m",
            "value": 1.005,
            "label": "Hidden flow label",
            "relation_type": "hidden-relation",
            "semantic_relation": "association",
            "style": "dashed",
            "bidirectional": True,
            "arrow_at_start": True,
            "arrow_at_end": True,
            "evidence_ids": ["line-a-m"],
        },
        {
            "id": "flow-b-m",
            "source": "b",
            "target": "m",
            "value": 2.335,
            "evidence_ids": ["line-b-m"],
        },
        {
            "id": "flow-m-t",
            "source": "m",
            "target": "t",
            "value": 2,
            "evidence_ids": ["line-m-t"],
        },
    ],
}

FALLBACK_SANKEY_IR = {
    "title": "Fallback heading",
    "description": "Fallback description",
    "direction": "RL",
    "nodes": [
        {
            "id": "A-B",
            "label": " 입력 ",
            "text": "Hidden input alias",
            "role": "hidden-role",
            "shape": "diamond",
            "bbox": [1, 2, 11, 12],
            "evidence_ids": ["ocr-input"],
        },
        {
            "id": "A B",
            "label": "출력",
            "bbox": [21, 2, 31, 12],
            "evidence_ids": ["ocr-output"],
        },
    ],
    "flows": [
        {
            "id": "flow-1",
            "source": "A-B",
            "target": "A B",
            "value": 3.125,
            "label": "Hidden fallback label",
            "relation_type": "hidden-relation",
            "semantic_relation": "association",
            "style": "dashed",
            "bidirectional": True,
            "arrow_at_start": True,
            "arrow_at_end": False,
            "evidence_ids": ["line-flow-1"],
        }
    ],
}


def test_native_sankey_scene_and_semantic_texts_match_the_terminal_canvas() -> None:
    serialized = serialize_sankey(NATIVE_SANKEY_IR)
    scene = typed_ir_to_scene(
        "sankey",
        NATIVE_SANKEY_IR,
        emitted_diagram_type=serialized.emitted_type,
    )

    assert serialized.emitted_type == "sankey"
    assert scene is not None
    assert scene.reading_direction == "LR"
    assert [
        (element.id, element.text, element.role, element.bbox, element.evidence_ids)
        for element in scene.elements
    ] == [
        ("a", "A", "node", (1, 2, 11, 12), ["ocr-a"]),
        ("b", "B", "node", (0, 0, 0, 0), ["ocr-b"]),
        ("m", "M", "node", (0, 0, 0, 0), ["ocr-m"]),
        ("t", "T", "node", (0, 0, 0, 0), ["ocr-t"]),
    ]
    assert all(element.shape != "diamond" for element in scene.elements)
    assert [
        (
            relation.id,
            relation.source_id,
            relation.target_id,
            relation.label,
            relation.relation_type,
            relation.semantic_relation,
            relation.arrow_at_start,
            relation.arrow_at_end,
            relation.line_style,
            relation.evidence_ids,
        )
        for relation in scene.relations
    ] == [
        (
            "flow-a-m",
            "a",
            "m",
            None,
            "generated_connector",
            "data_flow",
            False,
            False,
            None,
            ["line-a-m"],
        ),
        (
            "flow-b-m",
            "b",
            "m",
            None,
            "generated_connector",
            "data_flow",
            False,
            False,
            None,
            ["line-b-m"],
        ),
        (
            "flow-m-t",
            "m",
            "t",
            None,
            "generated_connector",
            "data_flow",
            False,
            False,
            None,
            ["line-m-t"],
        ),
    ]

    texts = list(
        typed_ir_semantic_texts(
            "sankey",
            NATIVE_SANKEY_IR,
            scene,
            emitted_diagram_type=serialized.emitted_type,
        )
    )
    assert Counter(texts) == Counter(["A", "1", "B", "2.34", "M", "3.34", "T", "2"])
    assert not {
        "Hidden native heading",
        "Hidden native description",
        "Hidden A alias",
        "Hidden flow label",
    }.intersection(texts)


def test_fallback_sankey_scene_uses_flowchart_ids_weights_and_direction() -> None:
    serialized = serialize_sankey(FALLBACK_SANKEY_IR)
    scene = typed_ir_to_scene(
        "sankey",
        FALLBACK_SANKEY_IR,
        emitted_diagram_type="flowchart-v2",
    )

    assert serialized.emitted_type == "flowchart"
    assert "A_B -->|3.125| A_B_2" in serialized.code
    assert scene is not None
    assert scene.reading_direction == "RL"
    assert [
        (element.id, element.text, element.role, element.bbox, element.evidence_ids)
        for element in scene.elements
    ] == [
        ("A_B", "입력", "node", (1, 2, 11, 12), ["ocr-input"]),
        ("A_B_2", "출력", "node", (21, 2, 31, 12), ["ocr-output"]),
    ]
    assert all(element.shape != "diamond" for element in scene.elements)
    [relation] = scene.relations
    assert (
        relation.id,
        relation.source_id,
        relation.target_id,
        relation.label,
        relation.relation_type,
        relation.semantic_relation,
        relation.arrow_at_start,
        relation.arrow_at_end,
        relation.line_style,
        relation.evidence_ids,
    ) == (
        "flow-1",
        "A_B",
        "A_B_2",
        "3.125",
        "generated_connector",
        "data_flow",
        False,
        True,
        None,
        ["line-flow-1"],
    )

    texts = list(
        typed_ir_semantic_texts(
            "sankey",
            FALLBACK_SANKEY_IR,
            scene,
            emitted_diagram_type="flowchart-v2",
        )
    )
    assert Counter(texts) == Counter(["입력", "출력", "3.125"])
    assert not {
        "Fallback heading",
        "Fallback description",
        "Hidden input alias",
        "Hidden fallback label",
    }.intersection(texts)


def test_sankey_native_total_keeps_js_fixed_point_precision_and_underflow_falls_back() -> None:
    large_ir = {
        "nodes": [
            {"id": "a", "label": "A"},
            {"id": "b", "label": "B"},
            {"id": "c", "label": "C"},
            {"id": "d", "label": "D"},
        ],
        "flows": [
            {
                "source": "a",
                "target": "b",
                "value": Decimal("12345678901234.56"),
            },
            {
                "source": "c",
                "target": "d",
                "value": Decimal("86029362697082.4"),
            },
        ],
    }
    large = serialize_sankey(large_ir)
    large_scene = typed_ir_to_scene(
        "sankey",
        large_ir,
        emitted_diagram_type=large.emitted_type,
    )

    assert large.emitted_type == "sankey"
    assert large_scene is not None
    assert Counter(
        typed_ir_semantic_texts(
            "sankey",
            large_ir,
            large_scene,
            emitted_diagram_type=large.emitted_type,
        )
    ) == Counter(
        [
            "A",
            "12345678901234.56",
            "B",
            "12345678901234.56",
            "C",
            "86029362697082.4",
            "D",
            "86029362697082.4",
        ]
    )

    underflow_ir = {
        "nodes": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
        "flows": [{"source": "a", "target": "b", "value": Decimal("1e-400")}],
    }
    underflow = serialize_sankey(underflow_ir)

    assert underflow.emitted_type == "flowchart"
    assert "0." + ("0" * 399) + "1" in underflow.code

    overflow_value = "1" + ("0" * 309)
    overflow_ir = {
        "nodes": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
        "flows": [{"source": "a", "target": "b", "value": Decimal("1e309")}],
    }
    overflow = serialize_sankey(overflow_ir)
    overflow_scene = typed_ir_to_scene(
        "sankey",
        overflow_ir,
        emitted_diagram_type=overflow.emitted_type,
    )

    assert overflow.emitted_type == "flowchart"
    assert overflow_value in overflow.code
    assert overflow_scene is not None
    assert Counter(
        typed_ir_semantic_texts(
            "sankey",
            overflow_ir,
            overflow_scene,
            emitted_diagram_type=overflow.emitted_type,
        )
    ) == Counter(["A", "B", overflow_value])

    rounded_input = serialize_sankey(
        {
            "nodes": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
            "flows": [
                {
                    "source": "a",
                    "target": "b",
                    "value": Decimal("90071992547409.91"),
                }
            ],
        }
    )

    assert rounded_input.emitted_type == "flowchart"
    assert "90071992547409.91" in rounded_input.code


def test_sankey_native_total_uses_d3_input_order_and_relation_ids_stay_unique() -> None:
    ir = {
        "nodes": [
            {"id": "a", "label": "A"},
            {"id": "b", "label": "B"},
            {"id": "c", "label": "C"},
        ],
        "flows": [
            {"id": "parallel", "source": "a", "target": "b", "value": Decimal("1e13")},
            {"id": "parallel", "source": "a", "target": "c", "value": Decimal("0.001")},
            {"source": "a", "target": "c", "value": Decimal("0.001")},
            {"source": "a", "target": "c", "value": Decimal("0.001")},
        ],
    }
    serialized = serialize_sankey(ir)
    scene = typed_ir_to_scene("sankey", ir, emitted_diagram_type=serialized.emitted_type)

    assert serialized.emitted_type == "sankey"
    assert scene is not None
    assert [relation.id for relation in scene.relations] == [
        "parallel",
        "parallel_2",
        "sankey_flow_3",
        "sankey_flow_4",
    ]
    assert Counter(
        typed_ir_semantic_texts(
            "sankey",
            ir,
            scene,
            emitted_diagram_type=serialized.emitted_type,
        )
    ) == Counter(["A", "10000000000000.01", "B", "10000000000000", "C", "0"])


def test_sankey_flow_budget_fails_before_serializer_and_scene_diverge() -> None:
    ir = {
        "nodes": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
        "flows": [
            {"source": "a", "target": "b", "value": 1} for _index in range(MAX_SCENE_RELATIONS + 1)
        ],
    }

    with pytest.raises(SerializationError, match="flow count exceeds"):
        serialize_sankey(ir)
    assert typed_ir_to_scene("sankey", ir) is None


def test_sankey_flowchart_fallback_honors_the_runtime_edge_limit() -> None:
    at_limit = {
        "nodes": [{"id": "a", "label": "입력"}, {"id": "b", "label": "출력"}],
        "flows": [
            {"source": "a", "target": "b", "value": 1}
            for _index in range(MAX_SANKEY_FLOWCHART_EDGES)
        ],
    }
    over_limit = {
        **at_limit,
        "flows": [
            *at_limit["flows"],
            {"source": "a", "target": "b", "value": 1},
        ],
    }

    assert serialize_sankey(at_limit).emitted_type == "flowchart"
    with pytest.raises(SerializationError, match="Mermaid edge limit of 500"):
        serialize_sankey(over_limit)
    assert typed_ir_to_scene("sankey", over_limit) is None

    native_over_limit = {
        **over_limit,
        "nodes": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
    }
    assert serialize_sankey(native_over_limit).emitted_type == "sankey"
    with pytest.raises(SerializationError, match="Mermaid edge limit of 500"):
        serialize_sankey(native_over_limit, native_runtime_valid=False)


@pytest.mark.parametrize(
    ("ir", "message"),
    [
        ({"nodes": [], "flows": []}, "nodes requires a non-empty list"),
        (
            {"nodes": [{"id": "a", "label": "A"}], "flows": []},
            "flows requires a non-empty list",
        ),
    ],
)
def test_sankey_empty_required_records_fail_consistently(
    ir: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(SerializationError, match=message):
        serialize_sankey(ir)
    assert typed_ir_to_scene("sankey", ir) is None


@pytest.mark.parametrize(
    "invalid_evidence",
    ["ocr-a", 7, ["evidence"] * (MAX_EVIDENCE_REFS + 1), [""]],
)
def test_sankey_invalid_record_provenance_is_isolated_from_scene(
    invalid_evidence: object,
) -> None:
    ir = {
        "nodes": [
            {"id": "a", "label": "A", "evidence_ids": invalid_evidence},
            {"id": "b", "label": "B", "evidence_ids": ["ocr-b"]},
        ],
        "flows": [
            {
                "source": "a",
                "target": "b",
                "value": 1,
                "evidence_ids": invalid_evidence,
            }
        ],
    }

    serialized = serialize_sankey(ir)
    scene = typed_ir_to_scene("sankey", ir, emitted_diagram_type=serialized.emitted_type)

    assert serialized.emitted_type == "sankey"
    assert scene is not None
    assert scene.elements[0].evidence_ids == []
    assert scene.elements[1].evidence_ids == ["ocr-b"]
    assert scene.relations[0].evidence_ids == []


@pytest.mark.parametrize("invalid_id", [7, "x" * (MAX_ID_CHARS + 1), "\ud800"])
def test_sankey_invalid_flow_scene_id_uses_a_deterministic_bounded_slot(
    invalid_id: object,
) -> None:
    ir = {
        "nodes": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
        "flows": [
            {"id": invalid_id, "source": "a", "target": "b", "value": 1},
            {"id": "sankey_flow_1", "source": "a", "target": "b", "value": 2},
        ],
    }

    serialized = serialize_sankey(ir)
    scene = typed_ir_to_scene("sankey", ir, emitted_diagram_type=serialized.emitted_type)

    assert serialized.emitted_type == "sankey"
    assert scene is not None
    assert [relation.id for relation in scene.relations] == ["sankey_flow_1", "sankey_flow_1_2"]


@pytest.mark.integration
def test_mermaid_11_16_sankey_native_and_fallback_svg_canvas_contract() -> None:
    native = serialize_sankey(NATIVE_SANKEY_IR)
    fallback = serialize_sankey(FALLBACK_SANKEY_IR)
    large = serialize_sankey(
        {
            "nodes": [
                {"id": "a", "label": "A"},
                {"id": "b", "label": "B"},
                {"id": "c", "label": "C"},
                {"id": "d", "label": "D"},
            ],
            "flows": [
                {
                    "source": "a",
                    "target": "b",
                    "value": Decimal("12345678901234.56"),
                },
                {
                    "source": "c",
                    "target": "d",
                    "value": Decimal("86029362697082.4"),
                },
            ],
        }
    )
    runtime = NodeMermaidRuntime()
    try:
        native_runtime = runtime.validate_and_render(native.code, 20)
        fallback_runtime = runtime.validate_and_render(fallback.code, 20)
        large_runtime = runtime.validate_and_render(large.code, 20)
    finally:
        runtime.close()

    assert native_runtime.syntax_valid and native_runtime.render_valid
    assert fallback_runtime.syntax_valid and fallback_runtime.render_valid
    assert large_runtime.syntax_valid and large_runtime.render_valid
    assert native_runtime.diagram_type == "sankey"
    assert fallback_runtime.diagram_type == "flowchart-v2"
    assert native_runtime.svg is not None
    assert fallback_runtime.svg is not None
    assert large_runtime.svg is not None

    native_root = ET.fromstring(native_runtime.svg)
    fallback_root = ET.fromstring(fallback_runtime.svg)
    large_root = ET.fromstring(large_runtime.svg)
    native_canvas_texts = [
        "".join(element.itertext())
        for element in native_root.iter()
        if element.tag.rsplit("}", 1)[-1] == "text"
    ]
    fallback_canvas_texts = [
        "".join(element.itertext())
        for element in fallback_root.iter()
        if element.tag.rsplit("}", 1)[-1] == "text"
    ]
    large_canvas_texts = [
        "".join(element.itertext())
        for element in large_root.iter()
        if element.tag.rsplit("}", 1)[-1] == "text"
    ]
    assert Counter(native_canvas_texts) == Counter(["A\n1", "B\n2.34", "M\n3.34", "T\n2"])
    assert Counter(fallback_canvas_texts) == Counter(["입력", "출력", "3.125"])
    assert Counter(large_canvas_texts) == Counter(
        [
            "A\n12345678901234.56",
            "B\n12345678901234.56",
            "C\n86029362697082.4",
            "D\n86029362697082.4",
        ]
    )

    native_metadata = [
        (element.tag.rsplit("}", 1)[-1], "".join(element.itertext()))
        for element in native_root.iter()
        if element.tag.rsplit("}", 1)[-1] in {"title", "desc"}
    ]
    fallback_metadata = [
        (element.tag.rsplit("}", 1)[-1], "".join(element.itertext()))
        for element in fallback_root.iter()
        if element.tag.rsplit("}", 1)[-1] in {"title", "desc"}
    ]
    assert native_metadata == []
    assert fallback_metadata[0] == ("title", "Fallback heading")
    assert fallback_metadata[1][0] == "desc"
    assert fallback_metadata[1][1].startswith("Fallback description")

    assert not any(
        element.get("marker-start") or element.get("marker-end") for element in native_root.iter()
    )
    assert any(element.get("marker-end") for element in fallback_root.iter())


def test_native_sankey_rejection_retries_flowchart_in_the_same_candidate_slot() -> None:
    class SankeyRejectingRuntime:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def validate_and_render(self, code: str, timeout_seconds: float) -> RuntimeResult:
            self.calls.append(code)
            if code.startswith("sankey-beta"):
                return RuntimeResult(False, False, error="native Sankey parser rejected")
            return RuntimeResult(
                True,
                True,
                diagram_type="flowchart-v2",
                svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>',
            )

        def close(self) -> None:
            pass

    ir = {
        "direction": "RL",
        "nodes": [
            {
                "id": "A-B",
                "label": "Alpha",
                "evidence_ids": ["ocr-alpha"],
            },
            {
                "id": "A B",
                "label": "Beta",
                "evidence_ids": ["ocr-beta"],
            },
        ],
        "flows": [
            {
                "id": "flow-1",
                "source": "A-B",
                "target": "A B",
                "value": 2,
                "evidence_ids": ["line-flow-1"],
            }
        ],
    }
    observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["sankey"], scores=[1]),
        typed_candidates=[TypedIRCandidate(diagram_type="sankey", ir=ir)],
        evidence=[
            VisualEvidence(id="ocr-alpha", kind="ocr_token", text="Alpha"),
            VisualEvidence(id="ocr-beta", kind="ocr_token", text="Beta"),
            VisualEvidence(id="line-flow-1", kind="line_segment"),
        ],
    )
    runtime = SankeyRejectingRuntime()
    config = MermaidConfig(candidate_count=1)

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(runtime, config.security_profile),
    ).reconstruct(
        "sankey-runtime-fallback",
        "source.png",
        Image.new("RGB", (100, 100), "white"),
        ocr_texts=["Alpha Beta 2"],
    )

    assert result.selected is not None
    selected = result.selected
    assert selected.candidate_id == "candidate-1"
    assert result.alternatives == []
    assert len(runtime.calls) == 2
    assert runtime.calls[0].startswith("sankey-beta")
    assert runtime.calls[1].startswith("flowchart RL")
    assert "A_B -->|2| A_B_2" in runtime.calls[1]
    assert selected.diagram_type == "sankey"
    assert selected.emitted_diagram_type == "flowchart"
    assert selected.runtime_diagram_type == "flowchart-v2"
    assert selected.fallback_chain == ["sankey", "flowchart"]
    assert selected.generated_scene_ir is not None
    assert [element.id for element in selected.generated_scene_ir.elements] == ["A_B", "A_B_2"]
    [relation] = selected.generated_scene_ir.relations
    assert (relation.source_id, relation.target_id, relation.label) == ("A_B", "A_B_2", "2")
    assert not relation.arrow_at_start
    assert relation.arrow_at_end
    assert selected.scores["ocr_recall"] == 1
    assert selected.scores["numeric_consistency"] == 1
    assert selected.scores["visual_entailment_precision"] == 1
    assert any("same candidate slot" in warning for warning in selected.warnings)
    assert selected.repair_history[-1].operation == "runtime_portable_fallback"
    assert selected.repair_history[-1].accepted
