from __future__ import annotations

from copy import deepcopy

import pytest

from marker_mermaid.models import MAX_TEXT_CHARS
from marker_mermaid.serializers import (
    SerializationError,
    serialize_runtime_fallback_result,
    serialize_typed_ir_result,
)
from marker_mermaid.serializers_charts_sets import (
    plan_venn_records,
    serialize_venn,
    validate_venn_explicit_metadata,
)

METADATA_FIELDS = ("title", "description", "acc_title", "acc_description")

VENN_IR = {
    "sets": [
        {"id": "A", "label": "Buyers", "value": 10},
        {"id": "B", "label": "Members", "value": 8},
    ],
    "intersections": [{"id": "both", "sets": ["A", "B"], "label": "Both", "value": 3}],
}


def test_venn_explicit_metadata_allows_absent_fields() -> None:
    validate_venn_explicit_metadata(VENN_IR)


@pytest.mark.parametrize("field", METADATA_FIELDS)
def test_venn_explicit_metadata_allows_none(field: str) -> None:
    validate_venn_explicit_metadata({**VENN_IR, field: None})


@pytest.mark.parametrize("field", METADATA_FIELDS)
def test_venn_explicit_metadata_allows_exact_empty_as_omitted(field: str) -> None:
    validate_venn_explicit_metadata({**VENN_IR, field: ""})


def test_venn_plan_retains_exact_empty_compatibility_for_every_metadata_field() -> None:
    ir = {
        **deepcopy(VENN_IR),
        "title": "",
        "description": "",
        "acc_title": "",
        "acc_description": "",
    }

    plan = plan_venn_records(ir)
    code, emitted_type, fallback_reason = serialize_venn(ir)

    assert plan.semantic_title is None
    assert emitted_type == "venn"
    assert fallback_reason is None
    assert "title " not in code


@pytest.mark.parametrize("field", METADATA_FIELDS)
@pytest.mark.parametrize(
    "value",
    [
        "Observed title",
        "  결제 승인   reconstruction  ",
        "Architecture 🚀 summary",
    ],
)
def test_venn_explicit_metadata_allows_bounded_plain_text(
    field: str,
    value: str,
) -> None:
    validate_venn_explicit_metadata({**VENN_IR, field: value})


@pytest.mark.parametrize("field", METADATA_FIELDS)
def test_venn_explicit_metadata_allows_the_exact_text_limit(field: str) -> None:
    validate_venn_explicit_metadata({**VENN_IR, field: "x" * MAX_TEXT_CHARS})


@pytest.mark.parametrize("field", METADATA_FIELDS)
@pytest.mark.parametrize(
    ("value", "case"),
    [
        (True, "bool"),
        (0, "int"),
        (["text"], "list"),
        ({"text": "value"}, "dict"),
        (b"text", "bytes"),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_venn_explicit_metadata_rejects_non_text_values(
    field: str,
    value: object,
    case: str,
) -> None:
    del case
    with pytest.raises(SerializationError, match=rf"venn {field} must be text"):
        validate_venn_explicit_metadata({**VENN_IR, field: value})


@pytest.mark.parametrize("field", METADATA_FIELDS)
@pytest.mark.parametrize("value", [" ", "\u00a0", "\u2003"])
def test_venn_explicit_metadata_rejects_whitespace_only_text(
    field: str,
    value: str,
) -> None:
    with pytest.raises(SerializationError, match=rf"venn {field} must be bounded non-empty"):
        validate_venn_explicit_metadata({**VENN_IR, field: value})


@pytest.mark.parametrize("field", METADATA_FIELDS)
@pytest.mark.parametrize(
    ("value", "case"),
    [
        ("before\x00after", "null-control"),
        ("before\tafter", "tab-control"),
        ("before\u200bafter", "zero-width-format"),
        ("before\u2060after", "word-joiner-format"),
        ("before\u2028after", "line-separator"),
        ("before\u2029after", "paragraph-separator"),
    ],
    ids=lambda value: value if isinstance(value, str) and "-" in value else None,
)
def test_venn_explicit_metadata_rejects_control_and_format_text(
    field: str,
    value: str,
    case: str,
) -> None:
    del case
    with pytest.raises(SerializationError, match=rf"venn {field} contains unsupported text"):
        validate_venn_explicit_metadata({**VENN_IR, field: value})


@pytest.mark.parametrize("field", METADATA_FIELDS)
@pytest.mark.parametrize("line_break", ["\n", "\r", "\r\n"])
def test_venn_explicit_metadata_rejects_newline_laundering(
    field: str,
    line_break: str,
) -> None:
    with pytest.raises(SerializationError, match=rf"venn {field} contains unsupported text"):
        validate_venn_explicit_metadata({**VENN_IR, field: f"Visible{line_break}metadata"})


@pytest.mark.parametrize("field", METADATA_FIELDS)
def test_venn_explicit_metadata_rejects_lone_surrogates(field: str) -> None:
    with pytest.raises(SerializationError, match=rf"venn {field} is not valid UTF-8"):
        validate_venn_explicit_metadata({**VENN_IR, field: "before\ud800after"})


@pytest.mark.parametrize("field", METADATA_FIELDS)
def test_venn_explicit_metadata_rejects_oversized_text(field: str) -> None:
    with pytest.raises(SerializationError, match=rf"venn {field} must be bounded non-empty"):
        validate_venn_explicit_metadata({**VENN_IR, field: "x" * (MAX_TEXT_CHARS + 1)})


def test_venn_explicit_metadata_checks_raw_length_before_control_text() -> None:
    value = "x" * MAX_TEXT_CHARS + "\n"

    with pytest.raises(SerializationError, match="bounded non-empty"):
        validate_venn_explicit_metadata({**VENN_IR, "title": value})


@pytest.mark.parametrize("field", METADATA_FIELDS)
def test_venn_plan_rejects_raw_metadata_before_terminal_planning(field: str) -> None:
    ir = {**deepcopy(VENN_IR), field: ["laundered", "text"]}

    with pytest.raises(SerializationError, match=rf"venn {field} must be text"):
        plan_venn_records(ir)


def test_venn_native_serializer_enforces_raw_metadata_validation() -> None:
    ir = {**deepcopy(VENN_IR), "title": "Visible\nmetadata"}

    with pytest.raises(SerializationError, match="venn title contains unsupported text"):
        serialize_venn(ir)


def test_venn_fallback_serializer_enforces_raw_metadata_validation() -> None:
    ir = deepcopy(VENN_IR)
    del ir["sets"][1]["value"]
    ir["acc_description"] = {"text": "laundered"}

    with pytest.raises(SerializationError, match="venn acc_description must be text"):
        serialize_venn(ir)


@pytest.mark.parametrize(
    "serializer",
    [serialize_typed_ir_result, serialize_runtime_fallback_result],
    ids=["typed", "runtime-fallback"],
)
@pytest.mark.parametrize("field", METADATA_FIELDS)
def test_public_venn_wrappers_reject_raw_metadata_before_enrichment(
    serializer,
    field: str,
) -> None:
    ir = {**deepcopy(VENN_IR), field: "Visible\nmetadata"}

    with pytest.raises(SerializationError, match=rf"venn {field} contains unsupported text"):
        serializer("venn", ir, experimental=True)


def test_public_venn_wrappers_keep_exact_empty_compatibility() -> None:
    ir = {
        **deepcopy(VENN_IR),
        "title": "",
        "description": "",
        "acc_title": "",
        "acc_description": "",
    }

    native = serialize_typed_ir_result("venn", ir, experimental=True)
    fallback = serialize_runtime_fallback_result("venn", ir, experimental=True)

    assert native.emitted_type == "venn"
    assert fallback is not None
    assert fallback.emitted_type == "flowchart"
    assert "accTitle: Venn reconstruction" in fallback.code
    assert "Venn reconstruction containing Buyers, Members." in fallback.code
    assert "accDescr: This reconstruction is experimental" not in fallback.code
