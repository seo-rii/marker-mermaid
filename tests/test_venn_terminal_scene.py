from __future__ import annotations

from collections import Counter
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
    MAX_VENN_FLOWCHART_EDGES,
    plan_venn_records,
    serialize_venn,
)
from marker_mermaid.validation import CandidateValidator, NodeMermaidRuntime

NATIVE_VENN_IR = {
    "title": "Audience overlap",
    "description": "Hidden Venn description",
    "direction": "TB",
    "groups": [{"id": "hidden", "label": "Must not become a subgraph"}],
    "sets": [
        {
            "id": "buyers-premium",
            "label": "Buyers",
            "value": 10,
            "bbox": [0, 0, 30, 30],
            "evidence_ids": ["ocr-buyers", "circle-buyers"],
        },
        {
            "id": "members",
            "label": "Members",
            "value": 8,
            "bbox": [20, 0, 50, 30],
            "evidence_ids": ["ocr-members"],
        },
    ],
    "intersections": [
        {
            "id": "both-groups",
            "sets": ["buyers-premium", "members"],
            "label": "Both",
            "value": 3,
            "bbox": [20, 5, 30, 25],
            "evidence_ids": ["ocr-both", "area-both"],
        }
    ],
}


def test_native_venn_scene_and_semantic_texts_match_terminal_contract() -> None:
    code, emitted_type, fallback = serialize_venn(NATIVE_VENN_IR)
    scene = typed_ir_to_scene("venn", NATIVE_VENN_IR, emitted_diagram_type=emitted_type)

    assert code.startswith("venn-beta")
    assert 'set buyers_premium["Buyers"]: 10' in code
    assert 'union buyers_premium,members["Both"]: 3' in code
    assert emitted_type == "venn"
    assert fallback is None
    assert scene is not None
    assert scene.reading_direction == "unknown"
    assert [
        (item.id, item.text, item.role, item.shape, item.evidence_ids) for item in scene.elements
    ] == [
        ("buyers_premium", "Buyers", "set", "circle", ["ocr-buyers", "circle-buyers"]),
        ("members", "Members", "set", "circle", ["ocr-members"]),
        ("both_groups", "Both", "intersection", None, ["ocr-both", "area-both"]),
    ]
    assert all(item.bbox == (0, 0, 0, 0) for item in scene.elements)
    assert [
        (
            item.id,
            item.source_id,
            item.target_id,
            item.label,
            item.relation_type,
            item.semantic_relation,
            item.arrow_at_start,
            item.arrow_at_end,
            item.evidence_ids,
        )
        for item in scene.relations
    ] == [
        (
            "venn_relation_1",
            "buyers_premium",
            "both_groups",
            None,
            "logical_membership",
            "containment",
            False,
            False,
            ["ocr-both", "area-both"],
        ),
        (
            "venn_relation_2",
            "members",
            "both_groups",
            None,
            "logical_membership",
            "containment",
            False,
            False,
            ["ocr-both", "area-both"],
        ),
    ]
    assert list(
        typed_ir_semantic_texts(
            "venn",
            NATIVE_VENN_IR,
            scene,
            emitted_diagram_type=emitted_type,
        )
    ) == ["Audience overlap", "Buyers", "Members", "Both"]


def test_venn_flowchart_scene_and_semantic_texts_match_terminal_contract() -> None:
    code, emitted_type, fallback = serialize_venn(NATIVE_VENN_IR, native_runtime_valid=False)
    scene = typed_ir_to_scene("venn", NATIVE_VENN_IR, emitted_diagram_type=emitted_type)

    assert code.startswith("flowchart LR")
    assert "subgraph" not in code
    assert emitted_type == "flowchart"
    assert fallback is not None and "same candidate slot" in fallback
    assert scene is not None
    assert scene.reading_direction == "LR"
    assert [(item.id, item.text, item.role, item.shape) for item in scene.elements] == [
        ("buyers_premium", "Buyers (value: 10)", "set", "circle"),
        ("members", "Members (value: 8)", "set", "circle"),
        ("both_groups", "Both (value: 3)", "intersection", "round"),
    ]
    assert all(item.bbox == (0, 0, 0, 0) for item in scene.elements)
    assert [
        (item.source_id, item.target_id, item.label, item.arrow_at_start, item.arrow_at_end)
        for item in scene.relations
    ] == [
        ("buyers_premium", "both_groups", "intersects", False, True),
        ("members", "both_groups", "intersects", False, True),
    ]
    assert list(
        typed_ir_semantic_texts(
            "venn",
            NATIVE_VENN_IR,
            scene,
            emitted_diagram_type=emitted_type,
        )
    ) == [
        "Buyers (value: 10)",
        "Members (value: 8)",
        "Both (value: 3)",
        "intersects",
        "intersects",
    ]


def test_venn_higher_order_union_requires_every_explicit_pair() -> None:
    pair_only = {
        "sets": [
            {"id": "A", "label": "A", "value": 10},
            {"id": "B", "label": "B", "value": 9},
            {"id": "C", "label": "C", "value": 8},
        ],
        "intersections": [{"sets": ["A", "B"], "value": 4}],
    }
    incomplete = {
        "sets": [
            {"id": "A", "label": "A", "value": 10},
            {"id": "B", "label": "B", "value": 9},
            {"id": "C", "label": "C", "value": 8},
        ],
        "intersections": [
            {"sets": ["A", "B"], "value": 4},
            {"sets": ["A", "B", "C"], "label": "ABC", "value": 1},
        ],
    }
    complete = {
        **incomplete,
        "intersections": [
            {"sets": ["A", "B"], "value": 4},
            {"sets": ["A", "C"], "value": 3},
            {"sets": ["B", "C"], "value": 2},
            {"sets": ["A", "B", "C"], "label": "ABC", "value": 1},
        ],
    }

    pair_only_code, pair_only_type, pair_only_reason = serialize_venn(pair_only)
    incomplete_code, incomplete_type, reason = serialize_venn(incomplete)
    complete_code, complete_type, complete_reason = serialize_venn(complete)

    assert pair_only_type == "venn"
    assert pair_only_code.startswith("venn-beta")
    assert pair_only_reason is None
    assert incomplete_type == "flowchart"
    assert incomplete_code.startswith("flowchart LR")
    assert reason is not None and "pairwise" in reason
    assert complete_type == "venn"
    assert complete_code.startswith("venn-beta")
    assert complete_reason is None


@pytest.mark.parametrize(
    "ir",
    [
        {
            "sets": [
                {"id": "A", "label": "A", "value": 0},
                {"id": "B", "label": "B", "value": 2},
            ],
            "intersections": [{"sets": ["A", "B"], "label": "AB", "value": 0}],
        },
        {
            "sets": [
                {"id": "A", "label": "A", "value": 2},
                {"id": "B", "label": "B", "value": 2},
            ],
            "intersections": [{"sets": ["A", "B"], "label": "AB", "value": 0}],
        },
    ],
)
def test_zero_sized_venn_areas_use_exact_flowchart_fallback(ir: dict[str, object]) -> None:
    code, emitted_type, reason = serialize_venn(ir)

    assert emitted_type == "flowchart"
    assert reason is not None and "zero-sized" in reason
    assert "(value: 0)" in code


def test_exact_containment_uses_fallback_before_native_runtime_timeout() -> None:
    code, emitted_type, reason = serialize_venn(
        {
            "sets": [
                {"id": "A", "label": "A", "value": 10},
                {"id": "B", "label": "B", "value": 20},
            ],
            "intersections": [{"sets": ["A", "B"], "label": "A inside B", "value": 10}],
        }
    )

    assert emitted_type == "flowchart"
    assert reason is not None and "runtime budget" in reason
    assert "A inside B (value: 10)" in code


def test_native_venn_bounds_area_dynamic_range_for_visible_regions() -> None:
    at_limit = {
        "sets": [
            {"id": "small", "label": "Small", "value": 1},
            {"id": "large", "label": "Large", "value": 100},
        ],
        "intersections": [{"sets": ["small", "large"], "label": "Both", "value": 0.5}],
    }
    above_limit = {
        "sets": [
            {"id": "small", "label": "Small", "value": 1},
            {"id": "large", "label": "Large", "value": 100.5},
        ],
        "intersections": [{"sets": ["small", "large"], "label": "Both", "value": 0.5}],
    }
    invisible = {
        "sets": [
            {"id": "small", "label": "Small", "value": 1e-100},
            {"id": "large", "label": "Large", "value": 1e100},
        ],
        "intersections": [{"sets": ["small", "large"], "label": "Both", "value": 5e-101}],
    }

    assert serialize_venn(at_limit)[1] == "venn"
    assert serialize_venn(above_limit)[1] == "flowchart"
    code, emitted_type, reason = serialize_venn(invisible)
    assert emitted_type == "flowchart"
    assert reason is not None and "dynamic range" in reason
    assert "0.000" in code


def test_venn_uses_fixed_exact_numeric_tokens_and_rejects_impossible_supersets() -> None:
    exact = {
        "sets": [
            {"id": "A", "label": "A", "value": 2e-7},
            {"id": "B", "label": "B", "value": 3e-7},
        ],
        "intersections": [{"sets": ["A", "B"], "value": 1e-7}],
    }
    large_float = {
        "sets": [
            {"id": "A", "label": "A", "value": 1e20},
            {"id": "B", "label": "B", "value": 1e20},
        ],
        "intersections": [{"sets": ["A", "B"], "value": 5e19}],
    }
    unsafe_integer = {
        "sets": [
            {"id": "A", "label": "A", "value": 2**53},
            {"id": "B", "label": "B", "value": 2**53},
        ],
        "intersections": [{"sets": ["A", "B"], "value": 1}],
    }
    subnormal = {
        "sets": [
            {"id": "A", "label": "A", "value": 1e-323},
            {"id": "B", "label": "B", "value": 1e-323},
        ],
        "intersections": [{"sets": ["A", "B"], "value": 5e-324}],
    }
    precision = {
        "sets": [
            {"id": "A", "label": "A", "value": 2.0},
            {"id": "B", "label": "B", "value": 3.0},
        ],
        "intersections": [{"sets": ["A", "B"], "value": 1.2345678901234567}],
    }
    impossible = {
        "sets": [
            {"id": "A", "label": "A", "value": 10},
            {"id": "B", "label": "B", "value": 10},
            {"id": "C", "label": "C", "value": 10},
        ],
        "intersections": [
            {"sets": ["A", "B"], "value": 2},
            {"sets": ["A", "C"], "value": 2},
            {"sets": ["B", "C"], "value": 2},
            {"sets": ["A", "B", "C"], "value": 3},
        ],
    }

    exact_code, exact_type, _reason = serialize_venn(exact)
    large_code, large_type, _large_reason = serialize_venn(large_float)
    unsafe_code, unsafe_type, unsafe_reason = serialize_venn(unsafe_integer)
    subnormal_code, subnormal_type, subnormal_reason = serialize_venn(subnormal)
    precision_code, precision_type, _precision_reason = serialize_venn(precision)

    assert exact_type == "venn"
    assert ": 0.0000002" in exact_code
    assert ": 0.0000001" in exact_code
    assert "e-" not in exact_code.casefold()
    assert "e+" not in exact_code.casefold()
    assert large_type == "venn"
    assert ": 100000000000000000000" in large_code
    assert unsafe_type == "flowchart"
    assert unsafe_reason is not None and "non-binary64-safe" in unsafe_reason
    assert str(2**53) in unsafe_code
    assert subnormal_type == "flowchart"
    assert subnormal_reason is not None and "non-binary64-safe" in subnormal_reason
    assert "0.00000" in subnormal_code
    assert precision_type == "venn"
    assert ": 1.2345678901234567" in precision_code
    with pytest.raises(SerializationError, match="exceeds observed size of intersection"):
        serialize_venn(impossible)


def test_venn_large_integer_is_bounded_without_python_string_guard_leak() -> None:
    code, emitted_type, reason = serialize_venn(
        {
            "sets": [
                {"id": "A", "label": "A", "value": 10**5000},
                {"id": "B", "label": "B", "value": 10**5000},
            ],
            "intersections": [{"sets": ["A", "B"], "value": 1}],
        }
    )

    assert emitted_type == "flowchart"
    assert reason is not None and "non-binary64-safe" in reason
    assert len(code) > 10_000


@pytest.mark.parametrize(
    "invalid_evidence",
    ["ocr-a", 7, ["evidence"] * (MAX_EVIDENCE_REFS + 1), [""]],
)
def test_venn_invalid_record_provenance_is_isolated(invalid_evidence: object) -> None:
    ir = {
        "sets": [
            {"id": "A", "label": "A", "value": 2, "evidence_ids": invalid_evidence},
            {"id": "B", "label": "B", "value": 2, "evidence_ids": ["ocr-b"]},
        ],
        "intersections": [
            {
                "id": "both",
                "sets": ["A", "B"],
                "label": "Both",
                "value": 1,
                "evidence_ids": ["ocr-both"],
            }
        ],
    }

    scene = typed_ir_to_scene("venn", ir, emitted_diagram_type="venn")

    assert scene is not None
    assert [item.evidence_ids for item in scene.elements] == [[], ["ocr-b"], ["ocr-both"]]
    assert all(item.evidence_ids == ["ocr-both"] for item in scene.relations)


def test_venn_reserves_terminal_ids_across_set_and_intersection_collisions() -> None:
    plan = plan_venn_records(
        {
            "sets": [
                {"id": "intersection_1", "label": "A"},
                {"id": "B", "label": "B"},
            ],
            "intersections": [
                {"id": "shared-id", "sets": ["intersection_1", "B"], "label": "Shared"}
            ],
        }
    )

    assert [item.emitted_id for item in plan.sets] == ["intersection_1", "B"]
    assert [item.scene_id for item in plan.intersections] == ["shared_id"]
    collision_plan = plan_venn_records(
        {
            "sets": [{"id": "a-b", "label": "A"}, {"id": "a_b", "label": "B"}],
            "intersections": [{"sets": ["a-b", "a_b"]}],
        }
    )
    assert [item.emitted_id for item in collision_plan.sets] == ["a_b", "a_b_2"]


def test_venn_rejects_reused_records_and_bounds_ids() -> None:
    shared = {"id": "A", "label": "A", "value": 2}
    with pytest.raises(SerializationError, match="reuse one object"):
        plan_venn_records(
            {
                "sets": [shared, shared],
                "intersections": [{"sets": ["A", "A"], "value": 1}],
            }
        )
    with pytest.raises(SerializationError, match="bounded canonical id"):
        plan_venn_records(
            {
                "sets": [
                    {"id": "A" * (MAX_ID_CHARS + 1), "label": "A"},
                    {"id": "B", "label": "B"},
                ],
                "intersections": [{"sets": ["A", "B"]}],
            }
        )


def _venn_star_ir(pair_count: int, *, include_values: bool = False) -> dict[str, object]:
    return {
        "sets": [
            {"id": "A", "label": "A", **({"value": 10} if include_values else {})},
            *(
                {
                    "id": f"B{index}",
                    "label": f"B{index}",
                    **({"value": 10} if include_values else {}),
                }
                for index in range(pair_count)
            ),
        ],
        "intersections": [
            {
                "sets": ["A", f"B{index}"],
                "label": f"Shared {index}",
                **({"value": 1} if include_values else {}),
            }
            for index in range(pair_count)
        ],
    }


def test_venn_flowchart_fallback_honors_runtime_edge_limit() -> None:
    at_limit = _venn_star_ir(MAX_VENN_FLOWCHART_EDGES // 2)
    over_limit = _venn_star_ir(MAX_VENN_FLOWCHART_EDGES // 2)
    over_limit["intersections"].append(  # type: ignore[union-attr]
        {"sets": ["A", "B0", "B1"], "label": "Triple"}
    )

    assert plan_venn_records(at_limit).flowchart_supported
    assert serialize_venn(at_limit)[1] == "flowchart"
    assert not plan_venn_records(over_limit).flowchart_supported
    with pytest.raises(SerializationError, match="Mermaid edge limit of 500"):
        serialize_venn(over_limit)
    assert typed_ir_to_scene("venn", over_limit, emitted_diagram_type="flowchart-v2") is None


def test_venn_applies_500_edge_limit_only_to_flowchart_terminal() -> None:
    native_only = _venn_star_ir(MAX_VENN_FLOWCHART_EDGES // 2 + 1, include_values=True)
    plan = plan_venn_records(native_only)

    assert len(plan.memberships) == 502
    assert plan.native_supported
    assert not plan.flowchart_supported
    assert serialize_venn(native_only)[1] == "venn"
    assert typed_ir_to_scene("venn", native_only, emitted_diagram_type="venn") is not None
    assert typed_ir_to_scene("venn", native_only, emitted_diagram_type="flowchart-v2") is None
    with pytest.raises(SerializationError, match="Mermaid edge limit of 500"):
        serialize_venn(native_only, native_runtime_valid=False)


def test_venn_serializer_preflights_candidate_source_budget() -> None:
    ir = {
        "sets": [
            {"id": "A", "label": "A" * 50_000, "value": 2},
            {"id": "B", "label": "B", "value": 2},
        ],
        "intersections": [{"sets": ["A", "B"], "value": 1}],
    }

    with pytest.raises(SerializationError, match="source-character limit of 50000"):
        serialize_venn(ir)


def test_venn_compatibility_substitutions_are_disclosed_and_scanner_safe() -> None:
    ir = {
        "title": "T #1; <script> https://example.invalid",
        "acc_description": "Description <script> https://example.invalid",
        "sets": [
            {
                "id": "A",
                "label": (
                    'A  B\u00a0C #1; &unknown; "quote" \\ <script> '
                    "xhttps://example.invalid myIconify; click node; style node fill:red"
                ),
                "value": 2,
            },
            {"id": "B", "label": "Other", "value": 2},
        ],
        "intersections": [{"sets": ["A", "B"], "label": "Both", "value": 1}],
    }

    native = serialize_typed_ir_result("venn", ir)
    fallback_code, _type, fallback_reason = serialize_venn(ir, native_runtime_valid=False)

    assert any("compatibility glyphs" in warning for warning in native.warnings)
    assert fallback_reason is not None and "compatibility glyphs" in fallback_reason
    scanner = MermaidSecurityScanner(SecurityProfile.STRICT)
    assert scanner.scan(native.code).safe
    assert scanner.scan(fallback_code).safe


@pytest.mark.integration
def test_mermaid_11_16_venn_native_fallback_and_text_canvas_contract() -> None:
    compatibility_ir = {
        "title": "T #1; <script> https://example.invalid",
        "sets": [
            {
                "id": "A",
                "label": (
                    'A  B\u00a0C #1; &unknown; "quote" \\ <script> '
                    "xhttps://example.invalid myIconify; click node; style node fill:red"
                ),
                "value": 2,
            },
            {"id": "B", "label": "Other", "value": 2},
        ],
        "intersections": [{"id": "both", "sets": ["A", "B"], "label": "Both", "value": 1}],
    }
    native = serialize_venn(compatibility_ir)
    fallback = serialize_venn(compatibility_ir, native_runtime_valid=False)
    runtime = NodeMermaidRuntime()
    try:
        outcomes = [
            runtime.validate_and_render(code, 20) for code, _type, _reason in (native, fallback)
        ]
    finally:
        runtime.close()

    assert all(item.syntax_valid and item.render_valid for item in outcomes)
    assert [item.diagram_type for item in outcomes] == ["venn", "flowchart-v2"]
    roots = [ET.fromstring(item.svg or "") for item in outcomes]
    native_text = [
        "".join(item.itertext()).replace("\u200b", "")
        for item in roots[0].iter()
        if item.tag.rsplit("}", 1)[-1] == "text"
    ]
    fallback_text: list[str] = []
    for item in roots[1].iter():
        if item.tag.rsplit("}", 1)[-1] != "text":
            continue
        rows = [child for child in item if "row" in (child.get("class") or "").split()]
        value = (
            " ".join("".join(row.itertext()).strip() for row in rows)
            if rows
            else "".join(item.itertext())
        )
        fallback_text.append(value.replace("\u200b", ""))

    assert Counter(native_text) == Counter(
        [
            "T ＃1； ＜script＞ https://example.invalid",
            (
                "A B C #1; &unknown; ″quote″ \\ <script> "
                "xhttps://example.invalid myIconify; click node; style node fill:red"
            ),
            "Other",
            "Both",
        ]
    )
    assert Counter(fallback_text) == Counter(
        [
            (
                "A B C ＃1; &unknown; ″quote″ ∖ ＜script＞ "
                "xhttps://example.invalid myIconify; click node; style node fill:red (value: 2)"
            ),
            "Other (value: 2)",
            "Both (value: 1)",
            "intersects",
            "intersects",
        ]
    )
    assert not any(item.get("marker-end") for item in roots[0].iter())
    assert sum(bool(item.get("marker-end")) for item in roots[1].iter()) == 2


def test_native_venn_rejection_retries_flowchart_in_same_candidate_slot() -> None:
    class VennRejectingRuntime:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def validate_and_render(self, code: str, timeout_seconds: float) -> RuntimeResult:
            self.calls.append(code)
            if code.startswith("venn-beta"):
                return RuntimeResult(False, False, error="native Venn parser rejected")
            return RuntimeResult(
                True,
                True,
                diagram_type="flowchart-v2",
                svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>',
            )

        def close(self) -> None:
            pass

    ir = {
        "sets": [
            {
                "id": "A",
                "label": "Buyers",
                "value": 10,
                "evidence_ids": ["ocr-buyers", "circle-buyers"],
            },
            {"id": "B", "label": "Members", "value": 8, "evidence_ids": ["ocr-members"]},
        ],
        "intersections": [
            {
                "id": "both",
                "sets": ["A", "B"],
                "label": "Both",
                "value": 3,
                "evidence_ids": ["ocr-both", "area-both"],
            }
        ],
    }
    observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["venn"], scores=[1.0]),
        typed_candidates=[TypedIRCandidate(diagram_type="venn", ir=ir)],
        evidence=[
            VisualEvidence(id="ocr-buyers", kind="ocr_token", text="Buyers"),
            VisualEvidence(id="circle-buyers", kind="contour"),
            VisualEvidence(id="ocr-members", kind="ocr_token", text="Members"),
            VisualEvidence(id="ocr-both", kind="ocr_token", text="Both"),
            VisualEvidence(id="area-both", kind="contour"),
        ],
    )
    runtime = VennRejectingRuntime()
    config = MermaidConfig(candidate_count=1)

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(runtime, config.security_profile),
    ).reconstruct(
        "venn-runtime-fallback",
        "source.png",
        Image.new("RGB", (100, 100), "white"),
        ocr_texts=["Buyers value 10 Members value 8 Both value 3 intersects intersects"],
    )

    assert result.selected is not None
    selected = result.selected
    assert selected.candidate_id == "candidate-1"
    assert result.alternatives == []
    assert len(runtime.calls) == 2
    assert runtime.calls[0].startswith("venn-beta")
    assert runtime.calls[1].startswith("flowchart LR")
    assert "subgraph" not in runtime.calls[1]
    assert selected.diagram_type == "venn"
    assert selected.emitted_diagram_type == "flowchart"
    assert selected.runtime_diagram_type == "flowchart-v2"
    assert selected.fallback_chain == ["venn", "flowchart"]
    assert selected.generated_scene_ir is not None
    assert [item.id for item in selected.generated_scene_ir.elements] == ["A", "B", "both"]
    assert all(item.arrow_at_end for item in selected.generated_scene_ir.relations)
    assert selected.scores["ocr_recall"] == 1
    assert selected.scores["numeric_consistency"] == 1
    assert selected.scores["visual_entailment_precision"] == 1
    assert any("same candidate slot" in warning for warning in selected.warnings)
    assert selected.repair_history[-1].operation == "runtime_portable_fallback"
    assert selected.repair_history[-1].accepted
