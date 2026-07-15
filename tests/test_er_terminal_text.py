from __future__ import annotations

import copy
from xml.etree import ElementTree as ET

import pytest
from PIL import Image

from marker_mermaid.candidate_scene import typed_ir_semantic_texts, typed_ir_to_scene
from marker_mermaid.config import MermaidConfig, SecurityProfile
from marker_mermaid.engines import JsonFixtureEngine
from marker_mermaid.models import (
    MAX_TEXT_CHARS,
    DiagramTypePrediction,
    EngineObservation,
    TypedIRCandidate,
    VisualEvidence,
)
from marker_mermaid.pipeline import ReconstructionPipeline
from marker_mermaid.protocols import RepairProposal, RuntimeResult
from marker_mermaid.security import MermaidSecurityScanner
from marker_mermaid.serializers import SerializationError, serialize_typed_ir_result
from marker_mermaid.serializers_uml import (
    ER_TEXT_COMPATIBILITY_WARNING,
    enrich_er_accessibility_ir,
    plan_er_accessibility,
    plan_er_records,
    serialize_er,
    validated_er_accessibility_ir,
)
from marker_mermaid.validation import CandidateValidator, NodeMermaidRuntime


def _evidence(name: str) -> list[str]:
    return [f"evidence-{name}"]


def _er_ir(
    *,
    customer_id: str = "customer",
    customer_label: object = "Customer",
    relationship_label: object = "relates to",
) -> dict[str, object]:
    return {
        "entities": [
            {
                "id": customer_id,
                "label": customer_label,
                "evidence_ids": _evidence("customer"),
            },
            {
                "id": "order",
                "label": "Order",
                "evidence_ids": _evidence("order"),
            },
        ],
        "relationships": [
            {
                "id": "places",
                "source": customer_id,
                "target": "order",
                "source_cardinality": "one",
                "target_cardinality": "zero_or_more",
                "identifying": False,
                "label": relationship_label,
                "evidence_ids": _evidence("places"),
            }
        ],
    }


class _ERRuntime:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def validate_and_render(self, code: str, timeout_seconds: float) -> RuntimeResult:
        del timeout_seconds
        self.calls.append(code)
        return RuntimeResult(
            syntax_valid=True,
            render_valid=True,
            diagram_type="er",
            svg=(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50">'
                "<text>Customer Order</text></svg>"
            ),
        )

    def close(self) -> None:
        pass


class _ERLabelRepair:
    name = "er_label_repair"

    def __init__(self, label: str) -> None:
        self.label = label

    def repair(self, context: object, candidate: object) -> RepairProposal:
        del context
        typed_ir = copy.deepcopy(candidate.typed_ir)
        typed_ir["entities"][0]["label"] = self.label
        serialized = serialize_typed_ir_result("er", typed_ir, experimental=True)
        return RepairProposal(
            code=serialized.code,
            operation=self.name,
            typed_ir=typed_ir,
        )


class _ERMetadataRepair:
    name = "er_metadata_repair"

    def __init__(
        self,
        field: str,
        value: object,
        *,
        serialize: bool,
        entity_label: str | None = None,
    ) -> None:
        self.field = field
        self.value = value
        self.serialize = serialize
        self.entity_label = entity_label

    def repair(self, context: object, candidate: object) -> RepairProposal:
        del context
        typed_ir = copy.deepcopy(candidate.typed_ir)
        typed_ir[self.field] = self.value
        if self.entity_label is not None:
            typed_ir["entities"][0]["label"] = self.entity_label
        code = (
            serialize_typed_ir_result("er", typed_ir, experimental=True).code
            if self.serialize
            else f"{candidate.mermaid_code.rstrip()}\n    direction LR\n"
        )
        return RepairProposal(code=code, operation=self.name, typed_ir=typed_ir)


def _er_observation(
    *,
    customer_label: str,
    evidence_text: str,
    metadata: dict[str, object] | None = None,
) -> EngineObservation:
    ir = _er_ir(customer_label=customer_label)
    ir.update(metadata or {})
    ir["entities"][0]["bbox"] = [10, 10, 40, 40]
    ir["entities"][1]["bbox"] = [60, 10, 90, 40]
    return EngineObservation(
        prediction=DiagramTypePrediction(candidates=["er"], scores=[1]),
        typed_candidates=[TypedIRCandidate(diagram_type="er", ir=ir)],
        evidence=[
            VisualEvidence(
                id="evidence-customer",
                kind="ocr_token",
                text=evidence_text,
                bbox=(10, 10, 40, 40),
            ),
            VisualEvidence(
                id="evidence-order",
                kind="ocr_token",
                text="Order",
                bbox=(60, 10, 90, 40),
            ),
            VisualEvidence(id="evidence-places", kind="line_segment"),
        ],
    )


def test_er_relationship_role_is_quoted_as_one_terminal_label() -> None:
    code = serialize_er(_er_ir())

    assert 'customer ||..o{ order : "relates to"' in code


def test_er_plan_freezes_semantic_source_and_canvas_without_mutating_ir() -> None:
    ir = _er_ir(
        customer_label='  Customer\t"VIP" \\ path 100%\r\nnext  ',
        relationship_label='  relates\t"directly" to  ',
    )
    ir["entities"][0]["attributes"] = [
        {
            "type": "PK",
            "name": "customer id",
            "keys": ["PK", "UK"],
            "comment": 'stable "identifier"',
            "evidence_ids": _evidence("customer-id"),
        }
    ]
    original = copy.deepcopy(ir)

    plan = plan_er_records(ir)

    assert ir == original
    entity = plan.entities[0]
    attribute = entity.attributes[0]
    relationship = plan.relationships[0]
    assert entity.semantic_label == 'Customer "VIP" \\ path 100% next'
    assert entity.canvas_label == "Customer ″VIP″ ∖ path 100％ next"
    assert attribute.semantic_type == "PK"
    assert attribute.source_type == "`PK`"
    assert attribute.canvas_type == "PK"
    assert attribute.source_name == "`customer id`"
    assert attribute.canvas_name == "customer id"
    assert attribute.canvas_comment == "stable ″identifier″"
    assert relationship.semantic_label == 'relates "directly" to'
    assert relationship.canvas_label == "relates ″directly″ to"
    assert plan.compatibility_substitutions


def test_er_scene_and_ocr_projection_share_emitted_ids_and_canvas_text() -> None:
    ir = _er_ir(
        customer_id="erDiagram",
        customer_label='Customer "VIP"',
        relationship_label="relates **directly** to",
    )
    ir["entities"][0]["attributes"] = [
        {
            "type": "PK",
            "name": "customer id",
            "keys": ["PK"],
            "comment": "stable `identifier`",
            "evidence_ids": _evidence("customer-id"),
        }
    ]

    plan = plan_er_records(ir)
    scene = typed_ir_to_scene("er", ir)

    assert scene is not None
    assert [element.id for element in scene.elements] == [
        plan.entities[0].emitted_id,
        "order",
    ]
    assert [element.text for element in scene.elements] == [
        "Customer ″VIP″",
        "Order",
    ]
    assert scene.relations[0].id == plan.relationships[0].scene_id
    assert scene.relations[0].source_id == plan.entities[0].emitted_id
    assert scene.relations[0].target_id == "order"
    assert scene.relations[0].label == "relates ∗∗directly∗∗ to"
    assert scene.elements[0].evidence_ids == ["evidence-customer"]
    assert scene.relations[0].evidence_ids == ["evidence-places"]
    assert list(typed_ir_semantic_texts("er", ir, scene)) == [
        "Customer ″VIP″",
        "PK",
        "customer id",
        "PK",
        "stable ｀identifier｀",
        "Order",
        "relates ∗∗directly∗∗ to",
    ]


def test_er_relationship_scene_ids_are_unique_for_parallel_duplicate_ids() -> None:
    ir = _er_ir()
    duplicate = copy.deepcopy(ir["relationships"][0])
    duplicate["label"] = "also relates to"
    ir["relationships"].append(duplicate)

    plan = plan_er_records(ir)
    scene = typed_ir_to_scene("er", ir)

    assert [relationship.scene_id for relationship in plan.relationships] == [
        "places",
        "places_2",
    ]
    assert scene is not None
    assert [relationship.id for relationship in scene.relations] == ["places", "places_2"]


def test_er_source_only_neutralization_is_strict_safe_without_visible_warning() -> None:
    label = "Customer http://example.test callback(x) iconify click linkStyle config: --- @import"
    ir = _er_ir(customer_label=label, relationship_label="relates to")

    plan = plan_er_records(ir)
    result = serialize_typed_ir_result("er", ir)
    scene = typed_ir_to_scene("er", ir)

    assert "\u200b" in result.code
    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(result.code).safe
    assert plan.entities[0].canvas_label == label
    assert scene is not None and scene.elements[0].text == label
    assert not plan.compatibility_substitutions
    assert ER_TEXT_COMPATIBILITY_WARNING not in result.warnings


def test_er_result_warns_for_record_or_accessibility_canvas_substitutions() -> None:
    plain = serialize_typed_ir_result("er", _er_ir())
    record_compatibility = serialize_typed_ir_result(
        "er",
        _er_ir(customer_label='Customer "VIP"'),
    )
    accessibility_compatibility = serialize_typed_ir_result(
        "er",
        {**_er_ir(), "acc_title": "ER &#35; map"},
    )

    assert ER_TEXT_COMPATIBILITY_WARNING not in plain.warnings
    assert ER_TEXT_COMPATIBILITY_WARNING in record_compatibility.warnings
    assert ER_TEXT_COMPATIBILITY_WARNING in accessibility_compatibility.warnings


@pytest.mark.parametrize("field", ["title", "description", "acc_title", "acc_description"])
def test_er_exact_empty_accessibility_metadata_is_omitted_without_mutation(field: str) -> None:
    ir = {**_er_ir(), field: ""}
    original = copy.deepcopy(ir)

    validated = validated_er_accessibility_ir(ir)
    enriched = enrich_er_accessibility_ir(ir, experimental=False)
    result = serialize_typed_ir_result("er", ir)

    assert ir == original
    assert field not in validated
    if field in {"title", "description"}:
        assert field not in enriched
    else:
        assert enriched[field]
    assert enriched["acc_title"]
    assert enriched["acc_description"]
    assert "accTitle:" in result.code


@pytest.mark.parametrize("field", ["title", "description", "acc_title", "acc_description"])
@pytest.mark.parametrize(
    "value",
    [False, 0, [], {}, b"", " ", "A\nB", "A\x00B", "A\u200bB", "A\ud800B"],
)
def test_er_accessibility_metadata_rejects_malformed_raw_text(
    field: str,
    value: object,
) -> None:
    with pytest.raises(SerializationError, match="must be text|bounded|unsupported|UTF-8"):
        serialize_typed_ir_result("er", {**_er_ir(), field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("entity", " "),
        ("entity", False),
        ("entity", "A\x00B"),
        ("attribute_type", " "),
        ("attribute_name", []),
        ("attribute_comment", "\t"),
        ("relationship", {}),
        ("relationship", "A\u200bB"),
    ],
)
def test_er_visible_terminals_reject_malformed_typed_text(field: str, value: object) -> None:
    ir = _er_ir()
    if field == "entity":
        ir["entities"][0]["label"] = value
    elif field.startswith("attribute"):
        attribute = {
            "type": "uuid",
            "name": "customer_id",
            "comment": "stable identifier",
            "evidence_ids": _evidence("customer-id"),
        }
        attribute[field.removeprefix("attribute_")] = value
        ir["entities"][0]["attributes"] = [attribute]
    else:
        ir["relationships"][0]["label"] = value

    with pytest.raises(SerializationError, match="must be text|bounded|unsupported|UTF-8"):
        serialize_typed_ir_result("er", ir)


def test_er_bounded_text_limit_is_enforced_before_mermaid_runtime() -> None:
    ir = _er_ir(customer_label="x" * (MAX_TEXT_CHARS + 1))

    with pytest.raises(SerializationError, match="bounded"):
        serialize_er(ir)


def test_er_accessibility_plan_tracks_semantic_source_and_canvas_text() -> None:
    ir = {
        **_er_ir(),
        "acc_title": 'ER "map" \\ path &#35; **bold**',
        "acc_description": "Customer [link](target) &amp; Order",
    }

    record_plan = plan_er_records(ir)
    accessibility = plan_er_accessibility(ir, experimental=False, er_plan=record_plan)

    assert accessibility.title_semantic == 'ER "map" \\ path &#35; **bold**'
    assert accessibility.title_canvas == 'ER "map" \\ path ＆＃35; **bold**'
    assert accessibility.description_canvas == "Customer [link](target) &amp; Order"
    assert accessibility.compatibility_substitutions


def test_er_pipeline_rejects_raw_accessibility_metadata_before_runtime() -> None:
    observation = _er_observation(
        customer_label="Customer",
        evidence_text="Customer",
        metadata={"acc_title": " "},
    )
    config = MermaidConfig(candidate_count=1, publish_min_score=0)
    runtime = _ERRuntime()

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(runtime, config.security_profile),
    ).reconstruct("er-source", "source.png", Image.new("RGB", (100, 50), "white"))

    assert result.selected is None
    assert runtime.calls == []
    assert any(
        failure.stage == "serialization" and failure.error_type == "SerializationError"
        for failure in result.failures
    )


def test_er_repair_rejects_raw_accessibility_metadata_before_second_runtime() -> None:
    observation = _er_observation(customer_label="Customer", evidence_text="Customer")
    config = MermaidConfig(candidate_count=1, publish_min_score=0)
    runtime = _ERRuntime()

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(runtime, config.security_profile),
        repair_engine=_ERMetadataRepair("acc_title", " ", serialize=False),
    ).reconstruct("er-source", "source.png", Image.new("RGB", (100, 50), "white"))

    assert result.selected is not None
    assert len(runtime.calls) == 1
    assert not result.selected.repair_history[-1].accepted
    assert any(
        warning == "semantic repair IR could not be serialized: SerializationError"
        for warning in result.selected.warnings
    )


@pytest.mark.parametrize(
    ("initial_label", "repaired_label", "evidence_text", "expects_warning"),
    [
        ("Custmer", 'Customer "VIP"', "Customer VIP", True),
        ('Custmer "wrong"', "Customer", "Customer", False),
    ],
    ids=["repair-adds-compatibility", "repair-removes-compatibility"],
)
def test_er_accepted_repair_regenerates_accessibility_and_reconciles_warning(
    initial_label: str,
    repaired_label: str,
    evidence_text: str,
    expects_warning: bool,
) -> None:
    observation = _er_observation(
        customer_label=initial_label,
        evidence_text=evidence_text,
    )
    config = MermaidConfig(candidate_count=1, publish_min_score=0)
    runtime = _ERRuntime()

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(runtime, config.security_profile),
        repair_engine=_ERLabelRepair(repaired_label),
    ).reconstruct("er-source", "source.png", Image.new("RGB", (100, 50), "white"))

    assert result.selected is not None
    assert result.selected.repair_history[-1].accepted
    assert result.selected.typed_ir["entities"][0]["label"] == repaired_label
    assert "acc_title" not in result.selected.typed_ir
    assert "acc_description" not in result.selected.typed_ir
    assert "containing Customer" in result.selected.mermaid_code
    assert "containing Custmer" not in result.selected.mermaid_code
    assert (ER_TEXT_COMPATIBILITY_WARNING in result.selected.warnings) is expects_warning


@pytest.mark.integration
def test_er_mermaid_11_16_renders_one_multiword_relationship_without_phantom_entities() -> None:
    plan = plan_er_records(_er_ir())
    code = serialize_er(_er_ir())
    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)
    try:
        outcome = validator.validate(code, 20)
    finally:
        runtime.close()

    assert outcome.runtime.syntax_valid, outcome.runtime.error
    assert outcome.runtime.render_valid, outcome.runtime.error
    assert outcome.runtime.svg is not None
    root = ET.fromstring(outcome.runtime.svg)
    canvas_text = " ".join(" ".join(root.itertext()).split()).replace("\u200b", "")
    assert plan.relationships[0].canvas_label in canvas_text
    entity_groups = [
        element
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] == "g"
        and "node" in element.attrib.get("class", "").split()
    ]
    assert len(entity_groups) == len(plan.entities)


@pytest.mark.integration
def test_er_mermaid_11_16_svg_matches_terminal_and_accessibility_plans() -> None:
    ir = _er_ir(
        customer_label=(
            'Customer "VIP" \\ path 100% **bold** _ital_ `code` '
            "~~strike~~ [link](target) A &amp; B A &#35; B"
        ),
        relationship_label=(
            'relates "directly" to \\ path **bold** [link](target) http://example.test'
        ),
    )
    ir["entities"][0]["attributes"] = [
        {
            "type": "PK",
            "name": "customer id",
            "keys": ["PK", "UK"],
            "comment": 'stable "identifier" \\ path `code` [link](target)',
            "evidence_ids": _evidence("customer-id"),
        },
        {
            "type": "**kind**",
            "name": "[customer](target)",
            "keys": [],
            "evidence_ids": _evidence("customer-kind"),
        },
    ]
    ir["acc_title"] = 'ER "map" \\ path &#35; **bold**'
    ir["acc_description"] = "Customer [link](target) &amp; Order"
    plan = plan_er_records(ir)
    accessibility = plan_er_accessibility(ir, experimental=False, er_plan=plan)
    result = serialize_typed_ir_result("er", ir)
    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)
    try:
        outcome = validator.validate(result.code, 20)
    finally:
        runtime.close()

    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(result.code).safe
    assert outcome.runtime.syntax_valid, outcome.runtime.error
    assert outcome.runtime.render_valid, outcome.runtime.error
    assert outcome.runtime.svg is not None
    root = ET.fromstring(outcome.runtime.svg)
    title = next(element for element in root if element.tag.rsplit("}", 1)[-1] == "title")
    description = next(element for element in root if element.tag.rsplit("}", 1)[-1] == "desc")
    assert (title.text or "").replace("\u200b", "") == accessibility.title_canvas
    assert (description.text or "").replace("\u200b", "") == accessibility.description_canvas
    canvas_text = " ".join(
        " ".join("".join(element.itertext()).split())
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] == "text" and "".join(element.itertext()).strip()
    ).replace("\u200b", "")
    assert plan.entities[0].canvas_label in canvas_text
    for attribute in plan.entities[0].attributes:
        assert attribute.canvas_type in canvas_text
        assert attribute.canvas_name in canvas_text
        if attribute.canvas_comment is not None:
            assert attribute.canvas_comment in canvas_text
    assert plan.relationships[0].canvas_label in canvas_text
    assert ER_TEXT_COMPATIBILITY_WARNING in result.warnings


@pytest.mark.integration
def test_er_mermaid_11_16_renders_reserved_ids_with_emitted_scene_identity() -> None:
    reserved_ids = [
        "erDiagram",
        "style",
        "classDef",
        "class",
        "one",
        "many",
        "to",
        "click",
        "linkStyle",
        "iconify",
        "__proto__",
    ]
    ir = {
        "entities": [
            {
                "id": source_id,
                "label": f"Entity {index}",
                "evidence_ids": [f"entity-{index}"],
            }
            for index, source_id in enumerate(reserved_ids, start=1)
        ],
        "relationships": [
            {
                "source": reserved_ids[index - 1],
                "target": reserved_ids[index],
                "source_cardinality": "one",
                "target_cardinality": "zero_or_more",
                "identifying": False,
                "label": f"edge {index}",
                "evidence_ids": [f"edge-{index}"],
            }
            for index in range(1, len(reserved_ids))
        ],
    }
    plan = plan_er_records(ir)
    scene = typed_ir_to_scene("er", ir)
    code = serialize_er(ir)
    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)
    try:
        outcome = validator.validate(code, 20)
    finally:
        runtime.close()

    assert all(entity.emitted_id.startswith("mmx_er_id_") for entity in plan.entities)
    assert scene is not None
    assert [element.id for element in scene.elements] == [
        entity.emitted_id for entity in plan.entities
    ]
    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(code).safe
    assert outcome.runtime.syntax_valid, outcome.runtime.error
    assert outcome.runtime.render_valid, outcome.runtime.error
    assert outcome.runtime.svg is not None
    canvas_text = " ".join(" ".join(ET.fromstring(outcome.runtime.svg).itertext()).split())
    assert all(f"Entity {index}" in canvas_text for index in range(1, len(reserved_ids) + 1))
    assert all(f"edge {index}" in canvas_text for index in range(1, len(reserved_ids)))
