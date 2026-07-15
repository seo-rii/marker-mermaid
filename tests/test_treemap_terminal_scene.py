from __future__ import annotations

from collections import Counter
from decimal import Decimal
from xml.etree import ElementTree as ET

import pytest
from PIL import Image

from marker_mermaid.candidate_scene import typed_ir_semantic_texts, typed_ir_to_scene
from marker_mermaid.config import MermaidConfig, SecurityProfile
from marker_mermaid.engines import JsonFixtureEngine
from marker_mermaid.models import (
    MAX_ID_CHARS,
    DiagramTypePrediction,
    EngineObservation,
    TypedIRCandidate,
    VisualEvidence,
)
from marker_mermaid.pipeline import ReconstructionPipeline
from marker_mermaid.protocols import RuntimeResult
from marker_mermaid.resource_limits import MAX_EVIDENCE_REFS
from marker_mermaid.security import MermaidSecurityScanner
from marker_mermaid.serializers import SerializationError, serialize_typed_ir_result
from marker_mermaid.serializers_charts_sets import (
    MAX_TREEMAP_FLOWCHART_EDGES,
    plan_treemap_records,
    serialize_treemap,
)
from marker_mermaid.validation import CandidateValidator, NodeMermaidRuntime

NATIVE_TREEMAP_IR = {
    "title": "Portfolio overview",
    "description": "Hidden portfolio description",
    "direction": "RL",
    "root": {
        "id": "portfolio",
        "label": "Portfolio",
        "text": "Hidden root alias",
        "role": "hidden-role",
        "shape": "diamond",
        "bbox": [0, 0, 80, 80],
        "evidence_ids": ["ocr-portfolio"],
        "children": [
            {
                "id": "core",
                "label": "Core",
                "bbox": [2, 2, 60, 60],
                "evidence_ids": ["ocr-core"],
                "children": [
                    {
                        "id": "api",
                        "label": "API",
                        "value": 1000,
                        "evidence_ids": ["ocr-api"],
                    },
                    {
                        "id": "db",
                        "label": "Database",
                        "value": 2000.5,
                        "evidence_ids": ["ocr-db"],
                    },
                ],
            },
            {
                "id": "edge",
                "label": "Edge",
                "value": 1000,
                "evidence_ids": ["ocr-edge"],
            },
        ],
    },
}

FALLBACK_TREEMAP_IR = {
    "title": "Fallback heading",
    "description": "Fallback description",
    "direction": "LR",
    "root": {
        "id": "source-root",
        "label": "Portfolio",
        "value": 4000.5,
        "bbox": [0, 0, 80, 80],
        "evidence_ids": ["ocr-portfolio"],
        "children": [
            {
                "id": "source-core",
                "label": "Core",
                "value": 3000.5,
                "evidence_ids": ["ocr-core"],
                "children": [
                    {"label": "API", "value": 1000, "evidence_ids": ["ocr-api"]},
                    {
                        "label": "Database",
                        "value": 2000.5,
                        "evidence_ids": ["ocr-db"],
                    },
                ],
            },
            {"label": "Edge", "value": 1000, "evidence_ids": ["ocr-edge"]},
        ],
    },
}


def test_native_treemap_scene_and_semantic_texts_match_terminal_contract() -> None:
    code, emitted_type, fallback = serialize_treemap(NATIVE_TREEMAP_IR)
    scene = typed_ir_to_scene("treemap", NATIVE_TREEMAP_IR, emitted_diagram_type=emitted_type)

    assert code.startswith("treemap-beta")
    assert emitted_type == "treemap"
    assert fallback is None
    assert scene is not None
    assert scene.reading_direction == "unknown"
    assert [
        (element.id, element.text, element.role, element.shape, element.evidence_ids)
        for element in scene.elements
    ] == [
        ("portfolio", "Portfolio", "section", None, ["ocr-portfolio"]),
        ("core", "Core", "section", None, ["ocr-core"]),
        ("api", "API", "leaf", None, ["ocr-api"]),
        ("db", "Database", "leaf", None, ["ocr-db"]),
        ("edge", "Edge", "leaf", None, ["ocr-edge"]),
    ]
    assert all(element.bbox == (0, 0, 0, 0) for element in scene.elements)
    assert [
        (
            relation.id,
            relation.source_id,
            relation.target_id,
            relation.relation_type,
            relation.semantic_relation,
            relation.arrow_at_start,
            relation.arrow_at_end,
            relation.evidence_ids,
        )
        for relation in scene.relations
    ] == [
        (
            "treemap_relation_2",
            "portfolio",
            "core",
            "logical_containment",
            "containment",
            False,
            False,
            ["ocr-core"],
        ),
        (
            "treemap_relation_3",
            "core",
            "api",
            "logical_containment",
            "containment",
            False,
            False,
            ["ocr-api"],
        ),
        (
            "treemap_relation_4",
            "core",
            "db",
            "logical_containment",
            "containment",
            False,
            False,
            ["ocr-db"],
        ),
        (
            "treemap_relation_5",
            "portfolio",
            "edge",
            "logical_containment",
            "containment",
            False,
            False,
            ["ocr-edge"],
        ),
    ]

    texts = list(
        typed_ir_semantic_texts(
            "treemap", NATIVE_TREEMAP_IR, scene, emitted_diagram_type=emitted_type
        )
    )
    assert Counter(texts) == Counter(
        [
            "Portfolio overview",
            "Portfolio",
            "4,000.5",
            "Core",
            "3,000.5",
            "API",
            "1,000",
            "Database",
            "2,000.5",
            "Edge",
            "1,000",
        ]
    )
    assert not {
        "Hidden portfolio description",
        "Hidden root alias",
        "hidden-role",
    }.intersection(texts)


def test_flowchart_treemap_scene_uses_preorder_ids_value_labels_and_arrows() -> None:
    code, emitted_type, fallback = serialize_treemap(FALLBACK_TREEMAP_IR)
    scene = typed_ir_to_scene("treemap", FALLBACK_TREEMAP_IR, emitted_diagram_type=emitted_type)

    assert emitted_type == "flowchart"
    assert fallback is not None
    assert code.startswith("flowchart TB")
    assert 'N1["Portfolio (value: 4000.5)"]' in code
    assert "N1 --> N2" in code
    assert scene is not None
    assert scene.reading_direction == "TB"
    assert [
        (element.id, element.text, element.role, element.shape, element.evidence_ids)
        for element in scene.elements
    ] == [
        ("N1", "Portfolio (value: 4000.5)", "node", "rectangle", ["ocr-portfolio"]),
        ("N2", "Core (value: 3000.5)", "node", "rectangle", ["ocr-core"]),
        ("N3", "API (value: 1000)", "node", "rectangle", ["ocr-api"]),
        ("N4", "Database (value: 2000.5)", "node", "rectangle", ["ocr-db"]),
        ("N5", "Edge (value: 1000)", "node", "rectangle", ["ocr-edge"]),
    ]
    assert [
        (relation.source_id, relation.target_id, relation.arrow_at_start, relation.arrow_at_end)
        for relation in scene.relations
    ] == [
        ("N1", "N2", False, True),
        ("N2", "N3", False, True),
        ("N2", "N4", False, True),
        ("N1", "N5", False, True),
    ]
    assert Counter(
        typed_ir_semantic_texts(
            "treemap", FALLBACK_TREEMAP_IR, scene, emitted_diagram_type=emitted_type
        )
    ) == Counter(element.text for element in scene.elements)


def test_treemap_totals_follow_d3_reverse_binary64_sum_and_comma_format() -> None:
    ir = {
        "root": {
            "label": "Root",
            "children": [
                {
                    "label": "Group",
                    "children": [
                        {"label": "A", "value": 1.005},
                        {"label": "B", "value": 2.335},
                    ],
                },
                {"label": "C", "value": 0.1},
                {"label": "D", "value": 0.2},
                {"label": "Exact float", "value": 1.2345678901234567},
            ],
        }
    }

    plan = plan_treemap_records(ir)
    code, emitted_type, _fallback = serialize_treemap(ir)

    assert emitted_type == "treemap"
    assert [node.value_text for node in plan.nodes] == [
        None,
        None,
        "1.005",
        "2.335",
        "0.1",
        "0.2",
        "1.2345678901234567",
    ]
    assert [node.native_total_text for node in plan.nodes] == [
        "4.87456789012",
        "3.34",
        "1.005",
        "2.335",
        "0.1",
        "0.2",
        "1.23456789012",
    ]
    assert '"Exact float": 1.2345678901234567' in code

    half_up = plan_treemap_records(
        {
            "root": {
                "label": "Root",
                "children": [{"label": "Half", "value": 100000000000.5}],
            }
        }
    )
    assert [node.native_total_text for node in half_up.nodes] == [
        "100,000,000,001",
        "100,000,000,001",
    ]


@pytest.mark.parametrize(
    "value",
    [Decimal("1e-400"), Decimal("1e309"), 2**53],
)
def test_treemap_non_binary64_safe_values_fall_back_without_changing_tokens(
    value: Decimal | int,
) -> None:
    ir = {
        "root": {
            "label": "Root",
            "children": [{"label": "Observed", "value": value}],
        }
    }

    code, emitted_type, fallback = serialize_treemap(ir)
    expected = format(Decimal(str(value)), "f")

    assert emitted_type == "flowchart"
    assert fallback is not None and "non-binary64-safe" in fallback
    assert f"Observed (value: {expected})" in code


def test_treemap_compatibility_substitutions_are_disclosed_in_result_metadata() -> None:
    native = serialize_typed_ir_result(
        "treemap",
        {
            "root": {
                "label": "Root",
                "children": [{"label": 'Quoted "leaf"', "value": 1}],
            }
        },
    )
    fallback = serialize_typed_ir_result(
        "treemap",
        {
            "root": {
                "label": "Root <group>",
                "value": 1,
                "children": [{"label": "Leaf \\ path", "value": 1}],
            }
        },
    )
    accessible = serialize_typed_ir_result(
        "treemap",
        {
            "acc_title": "Accessible <group>",
            "root": {
                "label": "Root",
                "children": [{"label": "Leaf", "value": 1}],
            },
        },
    )

    assert native.emitted_type == "treemap"
    assert any("compatibility glyphs" in warning for warning in native.warnings)
    assert fallback.emitted_type == "flowchart"
    assert any("compatibility glyphs" in warning for warning in fallback.warnings)
    assert accessible.emitted_type == "treemap"
    assert any("accessibility metadata" in warning for warning in accessible.warnings)


@pytest.mark.parametrize(
    "invalid_evidence",
    ["ocr-root", 7, ["evidence"] * (MAX_EVIDENCE_REFS + 1), [""]],
)
def test_treemap_invalid_record_provenance_is_isolated(
    invalid_evidence: object,
) -> None:
    ir = {
        "root": {
            "id": "same",
            "label": "Root",
            "evidence_ids": invalid_evidence,
            "children": [
                {
                    "id": "same",
                    "label": "Leaf",
                    "value": 1,
                    "evidence_ids": ["ocr-leaf"],
                }
            ],
        }
    }

    scene = typed_ir_to_scene("treemap", ir)

    assert scene is not None
    assert [element.id for element in scene.elements] == ["treemap_node_1", "treemap_node_2"]
    assert scene.elements[0].evidence_ids == []
    assert scene.elements[1].evidence_ids == ["ocr-leaf"]
    assert scene.relations[0].evidence_ids == ["ocr-leaf"]


@pytest.mark.parametrize("invalid_id", [7, "x" * (MAX_ID_CHARS + 1), "\ud800", "internal\nnewline"])
def test_treemap_invalid_scene_ids_use_collision_safe_reserved_slots(
    invalid_id: object,
) -> None:
    ir = {
        "root": {
            "id": invalid_id,
            "label": "Root",
            "children": [
                {"id": "treemap_node_1", "label": "Leaf", "value": 1},
            ],
        }
    }

    plan = plan_treemap_records(ir)

    assert [node.scene_id for node in plan.nodes] == [
        "treemap_node_1_2",
        "treemap_node_1",
    ]
    assert all(len(node.scene_id) <= MAX_ID_CHARS for node in plan.nodes)


def test_treemap_cycle_and_reused_objects_fail_before_scene_diverges() -> None:
    cycle_root: dict[str, object] = {"label": "Cycle"}
    cycle_root["children"] = [cycle_root]
    shared_leaf = {"label": "Shared", "value": 1}
    reused = {"root": {"label": "Root", "children": [shared_leaf, shared_leaf]}}

    with pytest.raises(SerializationError, match="cycle"):
        serialize_treemap({"root": cycle_root})
    with pytest.raises(SerializationError, match="reuses"):
        serialize_treemap(reused)
    assert typed_ir_to_scene("treemap", {"root": cycle_root}) is None
    assert typed_ir_to_scene("treemap", reused) is None


def test_treemap_flowchart_fallback_honors_runtime_edge_limit() -> None:
    at_limit = {
        "root": {
            "label": "Root",
            "value": MAX_TREEMAP_FLOWCHART_EDGES,
            "children": [
                {"label": f"Leaf {index}", "value": 1}
                for index in range(MAX_TREEMAP_FLOWCHART_EDGES)
            ],
        }
    }
    over_limit_native = {
        "root": {
            "label": "Root",
            "children": [
                {"label": f"Leaf {index}", "value": 1}
                for index in range(MAX_TREEMAP_FLOWCHART_EDGES + 1)
            ],
        }
    }
    over_limit_fallback = {
        "root": {
            **over_limit_native["root"],
            "value": MAX_TREEMAP_FLOWCHART_EDGES + 1,
        }
    }

    assert serialize_treemap(at_limit)[1] == "flowchart"
    assert serialize_treemap(over_limit_native)[1] == "treemap"
    with pytest.raises(SerializationError, match="Mermaid edge limit of 500"):
        serialize_treemap(over_limit_native, native_runtime_valid=False)
    with pytest.raises(SerializationError, match="Mermaid edge limit of 500"):
        serialize_treemap(over_limit_fallback)
    assert typed_ir_to_scene("treemap", over_limit_native) is not None
    assert (
        typed_ir_to_scene("treemap", over_limit_native, emitted_diagram_type="flowchart-v2") is None
    )


def test_treemap_serializer_preflights_candidate_source_budget() -> None:
    ir = {
        "acc_title": "Treemap",
        "acc_description": "Bounded description",
        "root": {
            "label": "R" * 50_000,
            "children": [{"label": "Leaf", "value": 1}],
        },
    }

    with pytest.raises(SerializationError, match="source-character limit of 50000"):
        serialize_treemap(ir)


@pytest.mark.integration
def test_mermaid_11_16_treemap_native_fallback_and_text_canvas_contract() -> None:
    compatibility_ir = {
        "title": "T #1; <script> https://example.invalid",
        "root": {
            "label": "Root",
            "children": [
                {
                    "label": (
                        'A  B\u00a0C #1; &unknown; "quote" \\ <script> '
                        "xhttps://example.invalid myIconify; click node; style node fill:red"
                    ),
                    "value": 1,
                },
                {"label": "Other", "value": 1},
            ],
        },
    }
    native = serialize_treemap(NATIVE_TREEMAP_IR)
    fallback = serialize_treemap(FALLBACK_TREEMAP_IR)
    compatible_native = serialize_treemap(compatibility_ir)
    compatible_fallback = serialize_treemap(compatibility_ir, native_runtime_valid=False)
    cases = [native, fallback, compatible_native, compatible_fallback]
    assert all(
        MermaidSecurityScanner(SecurityProfile.STRICT).scan(code).safe
        for code, _emitted_type, _fallback in cases
    )

    runtime = NodeMermaidRuntime()
    try:
        outcomes = [runtime.validate_and_render(code, 20) for code, _type, _reason in cases]
    finally:
        runtime.close()

    assert all(outcome.syntax_valid and outcome.render_valid for outcome in outcomes)
    assert [outcome.diagram_type for outcome in outcomes] == [
        "treemap",
        "flowchart-v2",
        "treemap",
        "flowchart-v2",
    ]
    roots = [ET.fromstring(outcome.svg or "") for outcome in outcomes]
    canvas_texts: list[list[str]] = []
    for root in roots:
        values: list[str] = []
        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1] != "text" or "display: none" in (
                element.get("style") or ""
            ):
                continue
            rows = [child for child in element if "row" in (child.get("class") or "").split()]
            value = (
                " ".join("".join(row.itertext()).strip() for row in rows)
                if rows
                else "".join(element.itertext())
            ).replace("\u200b", "")
            if value:
                values.append(value)
        canvas_texts.append(values)

    assert Counter(canvas_texts[0]) == Counter(
        [
            "Portfolio overview",
            "Portfolio",
            "4,000.5",
            "Core",
            "3,000.5",
            "API",
            "1,000",
            "Database",
            "2,000.5",
            "Edge",
            "1,000",
        ]
    )
    assert Counter(canvas_texts[1]) == Counter(
        [
            "Portfolio (value: 4000.5)",
            "Core (value: 3000.5)",
            "API (value: 1000)",
            "Database (value: 2000.5)",
            "Edge (value: 1000)",
        ]
    )
    assert "T #1; ＜script＞ https://example.invalid" in canvas_texts[2]
    assert (
        "A B C #1; &unknown; ″quote″ \\ <script> xhttps://example.invalid myIconify; "
        "click node; style node fill:red" in canvas_texts[2]
    )
    assert (
        "A B C ＃1; &unknown; ″quote″ ∖ ＜script＞ xhttps://example.invalid myIconify; "
        "click node; style node fill:red (value: 1)" in canvas_texts[3]
    )

    native_metadata = [
        (element.tag.rsplit("}", 1)[-1], "".join(element.itertext()))
        for element in roots[0].iter()
        if element.tag.rsplit("}", 1)[-1] in {"title", "desc"}
    ]
    fallback_metadata = [
        (element.tag.rsplit("}", 1)[-1], "".join(element.itertext()))
        for element in roots[1].iter()
        if element.tag.rsplit("}", 1)[-1] in {"title", "desc"}
    ]
    assert native_metadata == [
        ("title", "Portfolio overview"),
        ("desc", "Hidden portfolio description"),
    ]
    assert fallback_metadata[0] == ("title", "Fallback heading")
    assert fallback_metadata[1] == ("desc", "Fallback description")
    assert not any(
        element.get("marker-start") or element.get("marker-end") for element in roots[0].iter()
    )
    assert any(element.get("marker-end") for element in roots[1].iter())


def test_native_treemap_rejection_retries_flowchart_in_same_candidate_slot() -> None:
    class TreemapRejectingRuntime:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def validate_and_render(self, code: str, timeout_seconds: float) -> RuntimeResult:
            self.calls.append(code)
            if code.startswith("treemap-beta"):
                return RuntimeResult(False, False, error="native Treemap parser rejected")
            return RuntimeResult(
                True,
                True,
                diagram_type="flowchart-v2",
                svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>',
            )

        def close(self) -> None:
            pass

    ir = {
        "root": {
            "id": "portfolio",
            "label": "Portfolio",
            "bbox": [5, 5, 95, 95],
            "evidence_ids": ["ocr-portfolio"],
            "children": [
                {
                    "id": "api",
                    "label": "API",
                    "value": 1000,
                    "bbox": [10, 30, 90, 90],
                    "evidence_ids": ["ocr-api", "box-api"],
                }
            ],
        }
    }
    observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["treemap"], scores=[1.0]),
        typed_candidates=[TypedIRCandidate(diagram_type="treemap", ir=ir)],
        evidence=[
            VisualEvidence(
                id="ocr-portfolio",
                kind="ocr_token",
                text="Portfolio",
                bbox=(10, 10, 80, 20),
            ),
            VisualEvidence(
                id="ocr-api",
                kind="ocr_token",
                text="API value 1000",
                bbox=(20, 40, 80, 55),
            ),
            VisualEvidence(id="box-api", kind="contour"),
        ],
    )
    runtime = TreemapRejectingRuntime()
    config = MermaidConfig(candidate_count=1)

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(runtime, config.security_profile),
    ).reconstruct(
        "treemap-runtime-fallback",
        "source.png",
        Image.new("RGB", (100, 100), "white"),
        ocr_texts=["Portfolio API value 1000"],
    )

    assert result.selected is not None
    selected = result.selected
    assert selected.candidate_id == "candidate-1"
    assert result.alternatives == []
    assert len(runtime.calls) == 2
    assert runtime.calls[0].startswith("treemap-beta")
    assert runtime.calls[1].startswith("flowchart TB")
    assert 'N2["API (value: 1000)"]' in runtime.calls[1]
    assert selected.diagram_type == "treemap"
    assert selected.emitted_diagram_type == "flowchart"
    assert selected.runtime_diagram_type == "flowchart-v2"
    assert selected.fallback_chain == ["treemap", "flowchart"]
    assert selected.generated_scene_ir is not None
    assert [element.id for element in selected.generated_scene_ir.elements] == ["N1", "N2"]
    [relation] = selected.generated_scene_ir.relations
    assert (relation.source_id, relation.target_id) == ("N1", "N2")
    assert not relation.arrow_at_start
    assert relation.arrow_at_end
    assert relation.evidence_ids == ["ocr-api", "box-api"]
    assert selected.scores["ocr_recall"] == 1
    assert selected.scores["numeric_consistency"] == 1
    assert selected.scores["visual_entailment_precision"] == 1
    assert any("same candidate slot" in warning for warning in selected.warnings)
    assert selected.repair_history[-1].operation == "runtime_portable_fallback"
    assert selected.repair_history[-1].accepted
