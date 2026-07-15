from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any

import pytest

from marker_mermaid.models import MAX_TEXT_CHARS
from marker_mermaid.serializers import (
    SerializationError,
    serialize_runtime_fallback_result,
    serialize_typed_ir_result,
)
from marker_mermaid.serializers_charts_sets import (
    plan_treemap_records,
    serialize_treemap,
    validate_treemap_explicit_metadata,
    validated_treemap_accessibility_ir,
)

METADATA_FIELDS = ("title", "description", "acc_title", "acc_description")

TREEMAP_IR = {
    "root": {
        "id": "root",
        "label": "Portfolio",
        "children": [
            {"id": "api", "label": "API", "value": 30},
            {"id": "database", "label": "Database", "value": 20},
        ],
    }
}


class _TextSubclass(str):
    pass


def _intrinsic_fallback(ir: dict[str, Any]) -> object:
    ir["root"]["value"] = 50
    return serialize_treemap(ir)


def _forced_fallback(ir: dict[str, Any]) -> object:
    return serialize_treemap(ir, native_runtime_valid=False)


def test_treemap_explicit_metadata_allows_absent_fields() -> None:
    validate_treemap_explicit_metadata(TREEMAP_IR)


@pytest.mark.parametrize("field", METADATA_FIELDS)
def test_treemap_explicit_metadata_allows_none(field: str) -> None:
    validate_treemap_explicit_metadata({**TREEMAP_IR, field: None})


@pytest.mark.parametrize("field", METADATA_FIELDS)
def test_treemap_explicit_metadata_allows_exact_empty_as_omitted(field: str) -> None:
    validate_treemap_explicit_metadata({**TREEMAP_IR, field: ""})


def test_validated_treemap_accessibility_ir_drops_exact_empty_without_mutating_source() -> None:
    ir = {
        **deepcopy(TREEMAP_IR),
        "title": "",
        "description": "",
        "acc_title": "",
        "acc_description": "",
    }

    sanitized = validated_treemap_accessibility_ir(ir)

    assert not set(METADATA_FIELDS) & sanitized.keys()
    assert all(ir[field] == "" for field in METADATA_FIELDS)


@pytest.mark.parametrize("field", METADATA_FIELDS)
@pytest.mark.parametrize(
    "value",
    [
        "Observed title",
        "  결제 승인   reconstruction  ",
        "Architecture 🚀 summary",
    ],
)
def test_treemap_explicit_metadata_allows_bounded_plain_text(
    field: str,
    value: str,
) -> None:
    validate_treemap_explicit_metadata({**TREEMAP_IR, field: value})


@pytest.mark.parametrize("field", METADATA_FIELDS)
def test_treemap_explicit_metadata_allows_the_exact_text_limit(field: str) -> None:
    validate_treemap_explicit_metadata({**TREEMAP_IR, field: "x" * MAX_TEXT_CHARS})


@pytest.mark.parametrize("field", METADATA_FIELDS)
@pytest.mark.parametrize(
    ("value", "case"),
    [
        (True, "bool"),
        (0, "int"),
        (["text"], "list"),
        ({"text": "value"}, "dict"),
        (b"text", "bytes"),
        (_TextSubclass("text"), "str-subclass"),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_treemap_explicit_metadata_rejects_non_builtin_text_values(
    field: str,
    value: object,
    case: str,
) -> None:
    del case
    with pytest.raises(SerializationError, match=rf"treemap {field} must be text"):
        validate_treemap_explicit_metadata({**TREEMAP_IR, field: value})


@pytest.mark.parametrize("field", METADATA_FIELDS)
@pytest.mark.parametrize("value", [" ", "\u00a0", "\u2003"])
def test_treemap_explicit_metadata_rejects_whitespace_only_text(
    field: str,
    value: str,
) -> None:
    with pytest.raises(SerializationError, match=rf"treemap {field} must be bounded non-empty"):
        validate_treemap_explicit_metadata({**TREEMAP_IR, field: value})


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
def test_treemap_explicit_metadata_rejects_control_and_format_text(
    field: str,
    value: str,
    case: str,
) -> None:
    del case
    with pytest.raises(SerializationError, match=rf"treemap {field} contains unsupported text"):
        validate_treemap_explicit_metadata({**TREEMAP_IR, field: value})


@pytest.mark.parametrize("field", METADATA_FIELDS)
@pytest.mark.parametrize("line_break", ["\n", "\r", "\r\n"])
def test_treemap_explicit_metadata_rejects_newline_laundering(
    field: str,
    line_break: str,
) -> None:
    with pytest.raises(SerializationError, match=rf"treemap {field} contains unsupported text"):
        validate_treemap_explicit_metadata({**TREEMAP_IR, field: f"Visible{line_break}metadata"})


@pytest.mark.parametrize("field", METADATA_FIELDS)
def test_treemap_explicit_metadata_rejects_lone_surrogates(field: str) -> None:
    with pytest.raises(SerializationError, match=rf"treemap {field} is not valid UTF-8"):
        validate_treemap_explicit_metadata({**TREEMAP_IR, field: "before\ud800after"})


@pytest.mark.parametrize("field", METADATA_FIELDS)
def test_treemap_explicit_metadata_rejects_oversized_text(field: str) -> None:
    with pytest.raises(SerializationError, match=rf"treemap {field} must be bounded non-empty"):
        validate_treemap_explicit_metadata({**TREEMAP_IR, field: "x" * (MAX_TEXT_CHARS + 1)})


def test_treemap_explicit_metadata_checks_raw_length_before_control_text() -> None:
    value = "x" * MAX_TEXT_CHARS + "\n"

    with pytest.raises(SerializationError, match="bounded non-empty"):
        validate_treemap_explicit_metadata({**TREEMAP_IR, "title": value})


@pytest.mark.parametrize(
    ("path", "serialize"),
    [
        ("plan", plan_treemap_records),
        ("native", serialize_treemap),
        ("intrinsic-fallback", _intrinsic_fallback),
        ("forced-fallback", _forced_fallback),
    ],
)
@pytest.mark.parametrize("field", METADATA_FIELDS)
def test_every_treemap_serialization_path_rejects_raw_metadata_before_resolution(
    path: str,
    serialize: Callable[[dict[str, Any]], object],
    field: str,
) -> None:
    del path
    ir = {**deepcopy(TREEMAP_IR), field: {"text": "laundered"}}

    with pytest.raises(SerializationError, match=rf"treemap {field} must be text"):
        serialize(ir)


def test_every_treemap_serialization_path_derives_accessibility_from_exact_empty() -> None:
    ir = {
        **deepcopy(TREEMAP_IR),
        "title": "",
        "description": "",
        "acc_title": "",
        "acc_description": "",
    }

    plan = plan_treemap_records(ir)
    native = serialize_treemap(ir)
    intrinsic_ir = deepcopy(ir)
    intrinsic_ir["root"]["value"] = 50
    intrinsic = serialize_treemap(intrinsic_ir)
    forced = serialize_treemap(ir, native_runtime_valid=False)

    assert plan.semantic_title is None
    assert native[1:] == ("treemap", None)
    assert intrinsic[1] == "flowchart"
    assert forced[1] == "flowchart"
    for code in (native[0], intrinsic[0], forced[0]):
        assert "accTitle: Treemap reconstruction" in code
        assert "Treemap reconstruction containing Portfolio, API, Database." in code
    assert "    title " not in native[0]


@pytest.mark.parametrize(
    "serializer",
    [serialize_typed_ir_result, serialize_runtime_fallback_result],
    ids=["typed", "runtime-fallback"],
)
@pytest.mark.parametrize("field", METADATA_FIELDS)
def test_public_treemap_wrappers_reject_raw_metadata_before_enrichment(
    serializer: Callable[..., object],
    field: str,
) -> None:
    ir = {**deepcopy(TREEMAP_IR), field: "Visible\nmetadata"}

    with pytest.raises(SerializationError, match=rf"treemap {field} contains unsupported text"):
        serializer("treemap", ir, experimental=True)


def test_public_treemap_wrappers_keep_exact_empty_compatibility() -> None:
    ir = {
        **deepcopy(TREEMAP_IR),
        "title": "",
        "description": "",
        "acc_title": "",
        "acc_description": "",
    }

    native = serialize_typed_ir_result("treemap", ir, experimental=True)
    fallback = serialize_runtime_fallback_result("treemap", ir, experimental=True)

    assert native.emitted_type == "treemap"
    assert "accTitle: Treemap reconstruction" in native.code
    assert fallback is not None
    assert fallback.emitted_type == "flowchart"
    assert "accTitle: Treemap reconstruction" in fallback.code
    assert "Treemap reconstruction containing Portfolio, API, Database." in fallback.code
    assert "accDescr: This reconstruction is experimental" not in fallback.code
