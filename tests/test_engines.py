from __future__ import annotations

from PIL import Image

from marker_mermaid.config import PHASE_ONE_TYPES
from marker_mermaid.engines import SYSTEM_PROMPT, MarkerStructuredVLMEngine
from marker_mermaid.models import DiagramTypePrediction, EngineObservation, VisualEvidence
from marker_mermaid.protocols import SourceContext
from marker_mermaid.typed_contracts import (
    PHASE_ONE_NESTED_TYPES,
    TYPED_IR_CONTRACTS,
    typed_ir_contract_prompt,
)


def test_system_prompt_requires_exact_scene_ids_and_prior_evidence_for_flow_nodes():
    prompt = " ".join(SYSTEM_PROMPT.split())

    assert "For flowchart and generic_network typed candidates" in prompt
    assert "exact IDs of matching scene_ir.elements from this same response" in prompt
    assert "do not rename, normalize, or invent node IDs" in prompt
    assert (
        "Every semantic typed node must include evidence_ids copied from supplied Prior evidence"
        in prompt
    )
    assert "reuse at least one of the same Prior evidence IDs" in prompt
    assert "Never self-declare or synthesize evidence IDs" in prompt


def test_marker_vlm_prompt_receives_prior_geometry_evidence():
    captured = {}

    def service(**kwargs):
        captured.update(kwargs)
        return EngineObservation(
            prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1.0])
        ).model_dump(mode="json")

    context = SourceContext(
        source_id="figure-1",
        source_block_ids=["/page/0/Figure/1"],
        source_image_name="figure.png",
        image=Image.new("RGB", (20, 20), "white"),
        views={"original": Image.new("RGB", (20, 20), "white")},
        evidence=[
            VisualEvidence(
                id="geometry-contour-001",
                kind="contour",
                bbox=(1, 1, 10, 10),
                score=0.9,
            )
        ],
        ocr_texts=["Label"],
    )

    result = MarkerStructuredVLMEngine(
        service,
        enabled_types={"flowchart", "architecture"},
    ).observe(context)

    assert result.prediction.candidates == ["flowchart"]
    assert "geometry-contour-001" in captured["prompt"]
    assert "Label" in captured["prompt"]
    assert "- flowchart: nodes:list" in captured["prompt"]
    assert "- architecture: services:list" in captured["prompt"]
    assert (
        "  architecture.services[]: {id:string,label:string,name:string,icon:string,"
        "group:string,bbox:number[4],evidence_ids:string[]}"
    ) in captured["prompt"]
    assert "source_side:L|R|T|B,target_side:L|R|T|B" in captured["prompt"]
    assert (
        "  flowchart.nodes[]: {id:string,label:string,text:string,role:string,shape:string,"
        "bbox:number[4],evidence_ids:string[]}"
    ) in captured["prompt"]
    assert "sequence.participants[]" not in captured["prompt"]
    assert "- packet:" not in captured["prompt"]
    assert '"name": "original"' in captured["prompt"]
    assert '"width": 20' in captured["prompt"]
    assert "exact IDs of matching\nscene_ir.elements from this same response" in captured["prompt"]
    assert "evidence_ids copied from supplied Prior evidence" in captured["prompt"]
    assert captured["image"] == [context.views["original"]]


def test_nested_contract_prompt_is_deterministic_and_enabled_type_only():
    first = typed_ir_contract_prompt({"timeline", "flowchart"})
    second = typed_ir_contract_prompt({"flowchart", "timeline"})

    assert first == second
    assert (
        "  flowchart.edges[]: {id:string,source:string,target:string,label:string,"
        "relation_type:string,semantic_relation:string,style:string,"
        "bidirectional:boolean,evidence_ids:string[]}"
    ) in first
    assert (
        "  timeline.events[]: {id:string,time:string,period:string,label:string,"
        "events:string[],bbox:number[4],evidence_ids:string[]}"
    ) in first
    assert "architecture.services[]" not in first
    assert "packet.fields[]" not in first
    assert first.index("- flowchart:") < first.index("- timeline:")


def test_every_phase_one_type_and_alias_has_nested_prompt_records():
    expected_records = {
        "architecture": ("services[]", "groups[]", "edges[]"),
        "bpmn": ("lanes[]", "lanes[].nodes[]", "edges[]"),
        "flowchart": ("nodes[]", "edges[]", "groups[]"),
        "gantt": ("sections[]", "sections[].tasks[]"),
        "generic_network": ("nodes[]", "edges[]", "groups[]"),
        "mindmap": ("root",),
        "sequence": ("participants[]", "messages[]"),
        "swimlane": ("lanes[]", "lanes[].nodes[]", "edges[]"),
        "timeline": ("events[]",),
    }

    expected_phase_one_types = PHASE_ONE_TYPES | {"generic_network"}
    assert expected_phase_one_types == PHASE_ONE_NESTED_TYPES
    assert set(expected_records) == PHASE_ONE_NESTED_TYPES
    for diagram_type, prefixes in expected_records.items():
        contract = TYPED_IR_CONTRACTS[diagram_type]
        prompt = typed_ir_contract_prompt({diagram_type})
        assert contract.nested_model is not None
        assert len(contract.prompt_records) == len(prefixes)
        assert tuple(record.split(":", 1)[0] for record in contract.prompt_records) == prefixes
        assert all(f"  {diagram_type}.{record}" in prompt for record in contract.prompt_records)
        assert all(
            f"  {other_type}." not in prompt
            for other_type in PHASE_ONE_NESTED_TYPES - {diagram_type}
        )
