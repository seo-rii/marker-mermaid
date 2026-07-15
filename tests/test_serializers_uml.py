from __future__ import annotations

import pytest

from marker_mermaid.config import SecurityProfile
from marker_mermaid.serializers import SerializationError
from marker_mermaid.serializers_uml import serialize_class, serialize_er, serialize_state
from marker_mermaid.validation import CandidateValidator, NodeMermaidRuntime


def evidence(number: int) -> list[str]:
    return [f"ocr-{number}"]


STATE_IR = {
    "title": "Order lifecycle",
    "description": "An order moves from pending to paid.",
    "direction": "LR",
    "states": [
        {"id": "pending", "label": 'Pending "review"', "evidence_ids": evidence(1)},
        {"id": "paid", "label": "Paid", "evidence_ids": evidence(2)},
    ],
    "transitions": [
        {"source": "[*]", "target": "pending", "evidence_ids": evidence(3)},
        {
            "source": "pending",
            "target": "paid",
            "label": "payment accepted",
            "evidence_ids": evidence(4),
        },
        {"source": "paid", "target": "[*]", "evidence_ids": evidence(5)},
    ],
}

CLASS_IR = {
    "title": "Checkout model",
    "classes": [
        {
            "id": "payment-service",
            "label": 'Payment "Service"',
            "evidence_ids": evidence(10),
            "members": [
                {
                    "name": "status",
                    "type": "String",
                    "visibility": "-",
                    "evidence_ids": evidence(11),
                },
                {
                    "name": "authorize",
                    "kind": "method",
                    "parameters": ["amount"],
                    "return_type": "bool",
                    "visibility": "+",
                    "evidence_ids": evidence(12),
                },
            ],
        },
        {"id": "gateway", "label": "Gateway", "evidence_ids": evidence(13)},
    ],
    "relations": [
        {
            "source": "payment-service",
            "target": "gateway",
            "type": "dependency",
            "label": "authorizes",
            "source_cardinality": "1",
            "target_cardinality": "0..*",
            "evidence_ids": evidence(14),
        }
    ],
}

ER_IR = {
    "title": "Ordering data",
    "entities": [
        {
            "id": "customer",
            "label": "Customer Account",
            "evidence_ids": evidence(20),
            "attributes": [
                {
                    "type": "uuid",
                    "name": "customer_id",
                    "keys": ["PK"],
                    "comment": 'stable "identifier"',
                    "evidence_ids": evidence(21),
                }
            ],
        },
        {"id": "order", "label": "Order", "evidence_ids": evidence(22)},
    ],
    "relationships": [
        {
            "source": "customer",
            "target": "order",
            "source_cardinality": "one",
            "target_cardinality": "zero_or_more",
            "identifying": False,
            "label": "places",
            "evidence_ids": evidence(23),
        }
    ],
}


def test_state_serializer_is_deterministic_and_preserves_explicit_boundaries():
    first = serialize_state(STATE_IR, experimental=True)
    assert first == serialize_state(STATE_IR, experimental=True)
    assert first.startswith("stateDiagram-v2\n")
    assert 'state "Pending ″review″" as pending' in first
    assert "[*] --> pending" in first
    assert "paid --> [*]" in first
    assert "experimental and requires review" in first


def test_class_serializer_emits_members_and_evidenced_relation():
    code = serialize_class(CLASS_IR)
    assert 'class payment_service["Payment &quot;Service&quot;"] {' in code
    assert "-String status" in code
    assert "+authorize(amount) bool" in code
    assert 'payment_service "1" ..> "0..*" gateway : authorizes' in code


def test_class_inheritance_uses_child_to_parent_input_semantics():
    ir = {
        "classes": [
            {"id": "child", "evidence_ids": evidence(1)},
            {"id": "parent", "evidence_ids": evidence(2)},
        ],
        "relations": [
            {
                "source": "child",
                "target": "parent",
                "type": "inheritance",
                "evidence_ids": evidence(3),
            }
        ],
    }
    assert "parent <|-- child" in serialize_class(ir)


def test_er_serializer_requires_and_preserves_cardinalities():
    code = serialize_er(ER_IR)
    assert 'customer["Customer Account"] {' in code
    assert 'uuid customer_id PK "stable &quot;identifier&quot;"' in code
    assert "customer ||..o{ order : places" in code


@pytest.mark.parametrize(
    ("serializer", "ir", "message"),
    [
        (
            serialize_state,
            {"states": [{"id": "orphan", "evidence_ids": []}]},
            "requires at least one evidence id",
        ),
        (
            serialize_state,
            {
                "states": [{"id": "known", "evidence_ids": evidence(1)}],
                "transitions": [
                    {"source": "known", "target": "invented", "evidence_ids": evidence(2)}
                ],
            },
            "unknown endpoint",
        ),
        (
            serialize_class,
            {
                "classes": [{"id": "A", "evidence_ids": evidence(1)}],
                "relations": [
                    {
                        "source": "A",
                        "target": "A",
                        "type": "guessed",
                        "evidence_ids": evidence(2),
                    }
                ],
            },
            "unsupported type",
        ),
        (
            serialize_er,
            {
                "entities": [
                    {"id": "A", "evidence_ids": evidence(1)},
                    {"id": "B", "evidence_ids": evidence(2)},
                ],
                "relationships": [
                    {
                        "source": "A",
                        "target": "B",
                        "identifying": True,
                        "label": "owns",
                        "evidence_ids": evidence(3),
                    }
                ],
            },
            "requires explicit cardinalities",
        ),
    ],
)
def test_serializers_reject_unevidenced_or_invented_structure(serializer, ir, message):
    with pytest.raises(SerializationError, match=message):
        serializer(ir)


@pytest.mark.integration
def test_uml_serializers_parse_and_render_in_mermaid_11_16():
    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)
    try:
        for code in (serialize_state(STATE_IR), serialize_class(CLASS_IR), serialize_er(ER_IR)):
            outcome = validator.validate(code, 20)
            assert outcome.runtime.syntax_valid, (code, outcome.runtime.error)
            assert outcome.runtime.render_valid, (code, outcome.runtime.error, outcome.warnings)
    finally:
        runtime.close()
