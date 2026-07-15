"""Strict typed serializers for Mermaid's core Phase 3 chart grammars.

The serializers in this module never estimate chart data.  Every slice value,
axis bound, series value, and quadrant point coordinate must be present in the
typed IR.  Like :mod:`marker_mermaid.serializers_phase2`, the public adapter
returns ``(code, emitted_type, fallback_reason)`` so callers can record which
Mermaid grammar was actually emitted.

Supported typed IR shapes
-------------------------

``pie``
    ``{"slices": [{"label": str, "value": number}], "show_data": bool}``

``xychart``
    ``x_axis`` is either categorical (``categories``) or numeric
    (explicit ``min`` and ``max``). ``y_axis`` always requires numeric bounds.
    Each series has ``kind`` (``line`` or ``bar``) and explicit ``values``.
    Numeric axes also accept explicit ``points``; because Mermaid 11.16 spaces
    XY values uniformly, their x coordinates must exactly match that uniform
    grid or serialization fails rather than distorting the data.

``quadrant``
    Both axes require explicit ``low`` and ``high`` labels.  Every point
    requires a label and normalized ``x`` and ``y`` coordinates in ``[0, 1]``.
"""

from __future__ import annotations

import math
import re
import sys
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, TypeAlias

from marker_mermaid.accessibility import resolve_accessibility
from marker_mermaid.config import SecurityProfile
from marker_mermaid.models import MAX_ID_CHARS, MAX_SCENE_ELEMENTS, MAX_TEXT_CHARS
from marker_mermaid.resource_limits import MAX_EVIDENCE_REFS
from marker_mermaid.security import MermaidSecurityScanner
from marker_mermaid.serializers import SerializationError

ChartCoreSerialization: TypeAlias = tuple[str, str, str | None]

MAX_PIE_NATIVE_SLICES = 12
MAX_PIE_FLOWCHART_SLICES = 256
MAX_PIE_OUTPUT_CHARS = 50_000
MAX_PIE_OUTPUT_LINES = 5_000
PIE_LABEL_RADIUS = 0.375

PIE_FALLBACK_WARNING = (
    "Pie was emitted as an exact-value Flowchart because Mermaid 11.16 would hide, clip, "
    "or rewrite one or more slices."
)
PIE_RUNTIME_FALLBACK_WARNING = (
    "CandidateValidator rejected native Pie; exact label/value cells were re-emitted as "
    "a portable Flowchart in the same candidate slot."
)
PIE_NATIVE_TEXT_COMPATIBILITY_WARNING = (
    "Pie canvas text used visible compatibility substitutions; semantic text remains in "
    "typed IR and review metadata."
)
PIE_FALLBACK_TEXT_COMPATIBILITY_WARNING = (
    "Pie Flowchart text used visible compatibility substitutions; semantic text remains "
    "in typed IR and review metadata."
)

_ZERO_WIDTH_SPACE = "\u200b"
_PIE_ACTIVE_CALLBACK = re.compile(r"\b(?:call|callback)\s*\(", re.IGNORECASE)
_PIE_CSS_IMPORT = re.compile(r"@import\b", re.IGNORECASE)
_PIE_DANGEROUS_SCHEME = re.compile(
    r"(?:https?|ftp|file|data|javascript):",
    re.IGNORECASE,
)
_PIE_REMOTE_ICON = re.compile(r"iconify|fa:|logos:", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class PieSlicePlan:
    """One exact source slice and its terminal-specific visible projection."""

    source_record: Mapping[str, Any]
    scene_id: str
    label: str
    native_source_label: str
    native_canvas_label: str
    native_legend_canvas_text: str
    fallback_source_label: str
    fallback_canvas_label: str
    value: Decimal
    value_text: str
    native_value: float | None
    percentage_text: str | None
    normalized_point: tuple[float, float] | None
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PiePlan:
    """Bounded terminal plan shared by Pie serialization, Scene, and OCR."""

    slices: tuple[PieSlicePlan, ...]
    show_data: bool
    native_supported: bool
    flowchart_supported: bool
    native_limitations: tuple[str, ...]
    semantic_title: str | None
    native_source_title: str | None
    native_canvas_title: str | None
    native_compatibility_substitutions: bool
    fallback_compatibility_substitutions: bool


def _text(value: Any) -> str:
    """Return deterministic single-line Mermaid text with quote-safe entities."""

    return (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', "&quot;")
        .replace("\r", " ")
        .replace("\n", " ")
        .strip()
    )


def _required_text(value: Any, *, field: str) -> str:
    text = _text(value) if value is not None else ""
    if not text:
        raise SerializationError(f"{field} requires non-empty text evidence")
    return text


def _accessibility(ir: dict[str, Any], diagram_type: str, *, experimental: bool) -> list[str]:
    resolved = resolve_accessibility(ir, diagram_type, experimental=experimental)
    return [
        f"    accTitle: {_text(resolved.title)}",
        f"    accDescr: {_text(resolved.description)}",
    ]


def _number(value: Any, *, field: str) -> tuple[Decimal, str]:
    """Validate and format a finite JSON-style number without float invention."""

    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        raise SerializationError(f"{field} requires an explicit numeric value")
    if isinstance(value, float) and not math.isfinite(value):
        raise SerializationError(f"{field} requires a finite numeric value")
    try:
        decimal = Decimal(value) if isinstance(value, int | Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise SerializationError(f"{field} requires a finite numeric value") from exc
    if not decimal.is_finite():
        raise SerializationError(f"{field} requires a finite numeric value")
    if decimal == 0:
        return Decimal(0), "0"
    if abs(decimal.adjusted()) >= MAX_TEXT_CHARS:
        raise SerializationError(f"{field} numeric token exceeds the source text limit")
    rendered = format(decimal, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    if len(rendered) > MAX_TEXT_CHARS:
        raise SerializationError(f"{field} numeric token exceeds the source text limit")
    return decimal, rendered


def _axis_bounds(axis: Any, *, field: str) -> tuple[Decimal, Decimal, str, str]:
    if not isinstance(axis, dict):
        raise SerializationError(f"{field} must be an object with explicit min and max")
    minimum, minimum_text = _number(axis.get("min"), field=f"{field}.min")
    maximum, maximum_text = _number(axis.get("max"), field=f"{field}.max")
    if minimum >= maximum:
        raise SerializationError(f"{field}.min must be smaller than {field}.max")
    return minimum, maximum, minimum_text, maximum_text


def _title_line(ir: dict[str, Any]) -> list[str]:
    title = ir.get("title")
    return [f"    title {_text(title)}"] if title else []


def validate_pie_explicit_metadata(ir: Mapping[str, Any]) -> None:
    """Reject explicit Pie metadata before accessibility enrichment can stringify it."""

    for field in ("title", "description", "acc_title", "acc_description"):
        value = ir.get(field)
        if value is not None and type(value) is not str:
            raise SerializationError(f"pie {field} must be text when provided")


def plan_pie_records(ir: Mapping[str, Any]) -> PiePlan:
    """Validate Pie records once and freeze native/fallback terminal semantics."""

    validate_pie_explicit_metadata(ir)
    slices = ir.get("slices")
    if not isinstance(slices, list) or not slices:
        raise SerializationError("pie IR requires a non-empty slices list")
    if len(slices) > min(MAX_SCENE_ELEMENTS, MAX_PIE_FLOWCHART_SLICES):
        raise SerializationError(
            f"Pie Flowchart fallback exceeds the {MAX_PIE_FLOWCHART_SLICES}-slice runtime limit"
        )
    show_data = ir.get("show_data", False)
    if not isinstance(show_data, bool):
        raise SerializationError("pie show_data must be a boolean")

    labels: set[str] = set()
    seen_records: set[int] = set()
    rows: list[dict[str, Any]] = []
    native_limitations: list[str] = []
    native_compatibility_substitutions = False
    fallback_compatibility_substitutions = False
    fallback_visible_translation = str.maketrans(
        {'"': "″", "\\": "∖", "<": "＜", ">": "＞", "#": "＃"}
    )
    for index, item in enumerate(slices, start=1):
        if not isinstance(item, Mapping):
            raise SerializationError("pie slices must be objects")
        identity = id(item)
        if identity in seen_records:
            raise SerializationError("pie slices cannot reuse one object")
        seen_records.add(identity)
        raw_label = item.get("label")
        if type(raw_label) is not str:
            raise SerializationError(f"pie slice {index}.label requires non-empty text evidence")
        label = " ".join(raw_label.split())
        if not label or len(label) > MAX_TEXT_CHARS:
            raise SerializationError(f"pie slice {index}.label requires bounded non-empty text")
        if any(
            unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"}
            for character in label
        ):
            raise SerializationError(f"pie slice {index}.label contains unsupported text")
        try:
            label.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise SerializationError(f"pie slice {index}.label is not valid UTF-8") from exc
        if label in labels:
            raise SerializationError(f"pie slice labels must be unique: {label!r}")
        labels.add(label)
        value, value_text = _number(item.get("value"), field=f"pie slice {index}.value")
        if value < 0:
            raise SerializationError(f"pie slice {index}.value cannot be negative")
        native_canvas_label = label
        native_source_label = native_canvas_label.replace(
            "%%", f"%{_ZERO_WIDTH_SPACE}%"
        ).replace("//", f"/{_ZERO_WIDTH_SPACE}/").replace(";", f";{_ZERO_WIDTH_SPACE}")
        native_source_label = native_source_label.replace("<", f"<{_ZERO_WIDTH_SPACE}")
        native_source_label = _PIE_CSS_IMPORT.sub(
            lambda match: f"{match.group(0)[:2]}{_ZERO_WIDTH_SPACE}{match.group(0)[2:]}",
            native_source_label,
        )
        native_source_label = _PIE_DANGEROUS_SCHEME.sub(
            lambda match: f"{match.group(0)[:-1]}{_ZERO_WIDTH_SPACE}:",
            native_source_label,
        )
        native_source_label = _PIE_REMOTE_ICON.sub(
            lambda match: f"{match.group(0)[0]}{_ZERO_WIDTH_SPACE}{match.group(0)[1:]}",
            native_source_label,
        )
        native_source_label = _PIE_ACTIVE_CALLBACK.sub(
            lambda match: f"{match.group(0)[0]}{_ZERO_WIDTH_SPACE}{match.group(0)[1:]}",
            native_source_label,
        )
        native_source_label = (
            native_source_label.replace("&", f"&{_ZERO_WIDTH_SPACE}")
            .replace("#", f"#{_ZERO_WIDTH_SPACE}")
            .replace("\\", "\\\\")
            .replace('"', '\\"')
        )
        fallback_canvas_label = label.translate(fallback_visible_translation)
        fallback_source_label = fallback_canvas_label.replace(
            "%%", f"%{_ZERO_WIDTH_SPACE}%"
        ).replace("//", f"/{_ZERO_WIDTH_SPACE}/").replace(";", f";{_ZERO_WIDTH_SPACE}")
        fallback_source_label = fallback_source_label.replace("<", f"<{_ZERO_WIDTH_SPACE}")
        fallback_source_label = _PIE_CSS_IMPORT.sub(
            lambda match: f"{match.group(0)[:2]}{_ZERO_WIDTH_SPACE}{match.group(0)[2:]}",
            fallback_source_label,
        )
        fallback_source_label = _PIE_DANGEROUS_SCHEME.sub(
            lambda match: f"{match.group(0)[:-1]}{_ZERO_WIDTH_SPACE}:",
            fallback_source_label,
        )
        fallback_source_label = _PIE_REMOTE_ICON.sub(
            lambda match: f"{match.group(0)[0]}{_ZERO_WIDTH_SPACE}{match.group(0)[1:]}",
            fallback_source_label,
        )
        fallback_source_label = _PIE_ACTIVE_CALLBACK.sub(
            lambda match: f"{match.group(0)[0]}{_ZERO_WIDTH_SPACE}{match.group(0)[1:]}",
            fallback_source_label,
        )
        fallback_source_label = (
            fallback_source_label.replace("&", f"&{_ZERO_WIDTH_SPACE}")
            .replace("#", f"#{_ZERO_WIDTH_SPACE}")
        )
        native_compatibility_substitutions |= native_canvas_label != raw_label
        fallback_compatibility_substitutions |= fallback_canvas_label != raw_label
        raw_evidence_ids = item.get("evidence_ids")
        if raw_evidence_ids is None:
            evidence_ids: tuple[str, ...] = ()
        elif (
            isinstance(raw_evidence_ids, list)
            and len(raw_evidence_ids) <= MAX_EVIDENCE_REFS
            and all(
                type(evidence_id) is str
                and bool(evidence_id)
                and len(evidence_id) <= MAX_ID_CHARS
                for evidence_id in raw_evidence_ids
            )
        ):
            try:
                for evidence_id in raw_evidence_ids:
                    evidence_id.encode("utf-8")
            except UnicodeEncodeError:
                evidence_ids = ()
            else:
                evidence_ids = tuple(raw_evidence_ids)
        else:
            evidence_ids = ()
        try:
            native_value = float(value_text)
        except (OverflowError, ValueError):
            native_value = None
        if native_value is not None and (
            not math.isfinite(native_value)
            or (value != 0 and native_value == 0)
            or (native_value != 0 and abs(native_value) < sys.float_info.min)
            or Decimal(str(native_value)) != value
        ):
            native_value = None
        if native_value is None:
            native_limitations.append(
                f"slice {index} is not zero-or-normal binary64 round-trip safe"
            )
        rows.append(
            {
                "source_record": item,
                "scene_id": f"pie_slice_{index}",
                "label": label,
                "native_source_label": native_source_label,
                "native_canvas_label": native_canvas_label,
                "fallback_source_label": fallback_source_label,
                "fallback_canvas_label": fallback_canvas_label,
                "value": value,
                "value_text": value_text,
                "native_value": native_value,
                "evidence_ids": evidence_ids,
            }
        )

    decimal_total = sum((row["value"] for row in rows), Decimal(0))
    if decimal_total <= 0:
        raise SerializationError("pie slices require a positive total")
    if len(rows) > MAX_PIE_NATIVE_SLICES:
        native_limitations.append(
            f"more than {MAX_PIE_NATIVE_SLICES} slices repeat the pinned color palette"
        )
    native_total = 0.0
    if all(row["native_value"] is not None for row in rows):
        for row in rows:
            native_total += row["native_value"]
    if not math.isfinite(native_total) or native_total <= 0:
        native_limitations.append("the pinned renderer would compute a non-finite Pie total")

    angle = 0.0
    native_rows: list[dict[str, Any]] = []
    if math.isfinite(native_total) and native_total > 0:
        angle_scale = 2 * math.pi / native_total
        for index, row in enumerate(rows, start=1):
            native_value = row["native_value"]
            percentage_text: str | None = None
            normalized_point: tuple[float, float] | None = None
            if native_value is not None and native_value > 0:
                percentage = native_value / native_total * 100
                if not math.isfinite(percentage) or percentage < 1:
                    native_limitations.append(
                        f"slice {index} falls below Mermaid's one-percent visibility threshold"
                    )
                else:
                    rounded_percentage = Decimal.from_float(percentage).quantize(
                        Decimal(1),
                        rounding=ROUND_HALF_UP,
                    )
                    percentage_text = f"{rounded_percentage}%"
                angle_span = native_value * angle_scale
                middle_angle = angle + angle_span / 2
                normalized_point = (
                    0.5 + PIE_LABEL_RADIUS * math.sin(middle_angle),
                    0.5 - PIE_LABEL_RADIUS * math.cos(middle_angle),
                )
                angle += angle_span
                if not all(math.isfinite(coordinate) for coordinate in normalized_point):
                    native_limitations.append(
                        f"slice {index} would create non-finite native geometry"
                    )
            legend_value_text = row["value_text"]
            if native_value is not None:
                python_number_text = repr(native_value).casefold()
                if "e" in python_number_text:
                    mantissa, raw_exponent = python_number_text.split("e", 1)
                    exponent = int(raw_exponent)
                    if 1e-6 <= abs(native_value) < 1e21:
                        python_number_text = format(Decimal(python_number_text), "f")
                    else:
                        mantissa = mantissa.removesuffix(".0")
                        exponent_text = f"+{exponent}" if exponent >= 0 else str(exponent)
                        python_number_text = f"{mantissa}e{exponent_text}"
                else:
                    python_number_text = python_number_text.removesuffix(".0")
                if show_data and python_number_text != row["value_text"]:
                    native_limitations.append(
                        f"slice {index} showData text would be rewritten by JavaScript"
                    )
                legend_value_text = python_number_text
            native_rows.append(
                {
                    **row,
                    "percentage_text": percentage_text,
                    "normalized_point": normalized_point,
                    "native_legend_canvas_text": (
                        f"{row['native_canvas_label']} [{legend_value_text}]"
                        if show_data
                        else row["native_canvas_label"]
                    ),
                }
            )
    else:
        native_rows = [
            {
                **row,
                "percentage_text": None,
                "normalized_point": None,
                "native_legend_canvas_text": row["native_canvas_label"],
            }
            for row in rows
        ]

    semantic_title: str | None = None
    native_source_title: str | None = None
    native_canvas_title: str | None = None
    raw_title = ir.get("title")
    if raw_title is not None and raw_title != "":
        if type(raw_title) is not str:
            raise SerializationError("pie title must be text")
        semantic_title = " ".join(raw_title.split())
        if not semantic_title or len(semantic_title) > MAX_TEXT_CHARS:
            raise SerializationError("pie title must be bounded non-empty text")
        if any(
            unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"}
            for character in semantic_title
        ):
            raise SerializationError("pie title contains unsupported text")
        try:
            semantic_title.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise SerializationError("pie title is not valid UTF-8") from exc
        native_canvas_title = semantic_title.translate(
            str.maketrans(
                {'"': "″", "\\": "∖", "<": "＜", ">": "＞", "#": "＃", ";": "；"}
            )
        )
        native_source_title = native_canvas_title.replace(
            "%%", f"%{_ZERO_WIDTH_SPACE}%"
        ).replace("//", f"/{_ZERO_WIDTH_SPACE}/")
        native_source_title = native_source_title.replace("<", f"<{_ZERO_WIDTH_SPACE}")
        native_source_title = _PIE_CSS_IMPORT.sub(
            lambda match: f"{match.group(0)[:2]}{_ZERO_WIDTH_SPACE}{match.group(0)[2:]}",
            native_source_title,
        )
        native_source_title = _PIE_DANGEROUS_SCHEME.sub(
            lambda match: f"{match.group(0)[:-1]}{_ZERO_WIDTH_SPACE}:",
            native_source_title,
        )
        native_source_title = _PIE_REMOTE_ICON.sub(
            lambda match: f"{match.group(0)[0]}{_ZERO_WIDTH_SPACE}{match.group(0)[1:]}",
            native_source_title,
        )
        native_source_title = _PIE_ACTIVE_CALLBACK.sub(
            lambda match: f"{match.group(0)[0]}{_ZERO_WIDTH_SPACE}{match.group(0)[1:]}",
            native_source_title,
        )
        native_source_title = (
            native_source_title.replace("&", f"&{_ZERO_WIDTH_SPACE}")
            .replace("#", f"#{_ZERO_WIDTH_SPACE}")
        )
        native_compatibility_substitutions |= native_canvas_title != raw_title

    native_supported = not native_limitations
    planned_slices = tuple(
        PieSlicePlan(
            source_record=row["source_record"],
            scene_id=row["scene_id"],
            label=row["label"],
            native_source_label=row["native_source_label"],
            native_canvas_label=row["native_canvas_label"],
            native_legend_canvas_text=row["native_legend_canvas_text"],
            fallback_source_label=f"{row['fallback_source_label']}: {row['value_text']}",
            fallback_canvas_label=f"{row['fallback_canvas_label']}: {row['value_text']}",
            value=row["value"],
            value_text=row["value_text"],
            native_value=row["native_value"],
            percentage_text=row["percentage_text"],
            normalized_point=row["normalized_point"],
            evidence_ids=row["evidence_ids"],
        )
        for row in native_rows
    )
    fallback_line_count = 3 + len(planned_slices)
    return PiePlan(
        slices=planned_slices,
        show_data=show_data,
        native_supported=native_supported,
        flowchart_supported=(
            len(planned_slices) <= MAX_PIE_FLOWCHART_SLICES
            and fallback_line_count + 1 <= MAX_PIE_OUTPUT_LINES
        ),
        native_limitations=tuple(dict.fromkeys(native_limitations)),
        semantic_title=semantic_title,
        native_source_title=native_source_title,
        native_canvas_title=native_canvas_title,
        native_compatibility_substitutions=native_compatibility_substitutions,
        fallback_compatibility_substitutions=fallback_compatibility_substitutions,
    )


def serialize_pie(
    ir: dict[str, Any],
    *,
    experimental: bool = False,
    native_runtime_valid: bool = True,
) -> ChartCoreSerialization:
    """Serialize explicit Pie evidence or an exact disconnected Flowchart fallback."""

    if not isinstance(native_runtime_valid, bool):
        raise SerializationError("native_runtime_valid must be a boolean")
    plan = plan_pie_records(ir)
    accessibility = resolve_accessibility(ir, "pie", experimental=experimental)
    accessibility_source: list[str] = []
    for value in (accessibility.title, accessibility.description):
        if not value or len(value) > MAX_TEXT_CHARS:
            raise SerializationError("Pie accessibility text must be bounded and non-empty")
        if any(
            unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"}
            for character in value
        ):
            raise SerializationError("Pie accessibility text contains unsupported text")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise SerializationError("Pie accessibility text is not valid UTF-8") from exc
        source_text = value.replace("%%", f"%{_ZERO_WIDTH_SPACE}%").replace(
            "//", f"/{_ZERO_WIDTH_SPACE}/"
        )
        source_text = source_text.replace("<", f"<{_ZERO_WIDTH_SPACE}")
        source_text = source_text.replace("#", f"#{_ZERO_WIDTH_SPACE}")
        source_text = _PIE_CSS_IMPORT.sub(
            lambda match: f"{match.group(0)[:2]}{_ZERO_WIDTH_SPACE}{match.group(0)[2:]}",
            source_text,
        )
        source_text = _PIE_DANGEROUS_SCHEME.sub(
            lambda match: f"{match.group(0)[:-1]}{_ZERO_WIDTH_SPACE}:",
            source_text,
        )
        source_text = _PIE_REMOTE_ICON.sub(
            lambda match: f"{match.group(0)[0]}{_ZERO_WIDTH_SPACE}{match.group(0)[1:]}",
            source_text,
        )
        source_text = _PIE_ACTIVE_CALLBACK.sub(
            lambda match: f"{match.group(0)[0]}{_ZERO_WIDTH_SPACE}{match.group(0)[1:]}",
            source_text,
        )
        accessibility_source.append(source_text)
    use_fallback = not native_runtime_valid or not plan.native_supported
    fallback_reason: str | None = None
    if use_fallback:
        if not plan.flowchart_supported:
            raise SerializationError(
                f"Pie Flowchart fallback exceeds the {MAX_PIE_FLOWCHART_SLICES}-slice runtime limit"
            )
        lines = [
            "flowchart TB",
            f"    accTitle: {accessibility_source[0]}",
            f"    accDescr: {accessibility_source[1]} "
            "This Pie reconstruction uses an exact-value Flowchart fallback.",
        ]
        lines.extend(
            f'    {slice_plan.scene_id}["{slice_plan.fallback_source_label}"]'
            for slice_plan in plan.slices
        )
        fallback_reason = (
            PIE_RUNTIME_FALLBACK_WARNING
            if not native_runtime_valid
            else f"{PIE_FALLBACK_WARNING} {'; '.join(plan.native_limitations)}"
        )
        emitted_type = "flowchart"
    else:
        lines = ["pie showData" if plan.show_data else "pie"]
        lines.extend(
            [
                f"    accTitle: {accessibility_source[0]}",
                f"    accDescr: {accessibility_source[1]}",
            ]
        )
        if plan.native_source_title is not None:
            lines.append(f"    title {plan.native_source_title}")
        lines.extend(
            f'    "{slice_plan.native_source_label}" : {slice_plan.value_text}'
            for slice_plan in plan.slices
        )
        emitted_type = "pie"

    code = "\n".join(lines) + "\n"
    if code.count("\n") + 1 > MAX_PIE_OUTPUT_LINES:
        raise SerializationError(
            f"Pie output exceeds source-line limit of {MAX_PIE_OUTPUT_LINES}"
        )
    try:
        utf16_code_units = len(code.encode("utf-16-le")) // 2
    except UnicodeEncodeError as exc:
        raise SerializationError("Pie output is not valid UTF-16") from exc
    if utf16_code_units > MAX_PIE_OUTPUT_CHARS:
        raise SerializationError(
            f"Pie output exceeds UTF-16 source-character limit of {MAX_PIE_OUTPUT_CHARS}"
        )
    report = MermaidSecurityScanner(SecurityProfile.STRICT).scan(code)
    if not report.safe:
        rules = ", ".join(sorted({finding.rule for finding in report.findings}))
        raise SerializationError(f"Pie text violates the strict security profile: {rules}")
    return code, emitted_type, fallback_reason


def _xy_series_values(
    series: dict[str, Any],
    *,
    index: int,
    x_min: Decimal | None,
    x_max: Decimal | None,
    y_min: Decimal,
    y_max: Decimal,
) -> list[str]:
    has_values = "values" in series
    has_points = "points" in series
    if has_values == has_points:
        raise SerializationError(f"xychart series {index} requires exactly one of values or points")
    if has_values:
        values = series["values"]
        if not isinstance(values, list) or not values:
            raise SerializationError(f"xychart series {index}.values must be a non-empty list")
        rendered: list[str] = []
        for offset, value in enumerate(values):
            number, number_text = _number(
                value,
                field=f"xychart series {index}.values[{offset}]",
            )
            if not y_min <= number <= y_max:
                raise SerializationError(
                    f"xychart series {index}.values[{offset}] must be within y_axis bounds"
                )
            rendered.append(number_text)
        return rendered

    if x_min is None or x_max is None:
        raise SerializationError("xychart points require a numeric x_axis")
    points = series["points"]
    if not isinstance(points, list) or len(points) < 2:
        raise SerializationError(
            f"xychart series {index}.points requires at least two explicit coordinates"
        )
    coordinates: list[tuple[Decimal, str]] = []
    for offset, point in enumerate(points):
        if not isinstance(point, dict):
            raise SerializationError(f"xychart series {index}.points must be objects")
        x, _ = _number(point.get("x"), field=f"xychart series {index}.points[{offset}].x")
        y, y_text = _number(
            point.get("y"),
            field=f"xychart series {index}.points[{offset}].y",
        )
        if not y_min <= y <= y_max:
            raise SerializationError(
                f"xychart series {index}.points[{offset}].y must be within y_axis bounds"
            )
        coordinates.append((x, y_text))
    intervals = len(coordinates) - 1
    step = (x_max - x_min) / intervals
    for offset, (x, _) in enumerate(coordinates):
        expected = x_min + step * offset
        if x != expected:
            raise SerializationError(
                "Mermaid 11.16 xychart cannot preserve non-uniform x coordinates; "
                f"series {index} point {offset} is {x}, expected {expected}"
            )
    return [y for _, y in coordinates]


def serialize_xychart(ir: dict[str, Any], *, experimental: bool = False) -> ChartCoreSerialization:
    """Serialize explicit axes and line/bar values to native ``xychart-beta``."""

    x_axis = ir.get("x_axis")
    if not isinstance(x_axis, dict):
        raise SerializationError("xychart x_axis must be an object")
    categories = x_axis.get("categories")
    x_min: Decimal | None = None
    x_max: Decimal | None = None
    if categories is not None:
        if "min" in x_axis or "max" in x_axis:
            raise SerializationError("xychart x_axis cannot mix categories with numeric bounds")
        if not isinstance(categories, list) or not categories:
            raise SerializationError("xychart x_axis.categories must be a non-empty list")
        category_text = [
            _required_text(value, field=f"xychart x_axis.categories[{index}]")
            for index, value in enumerate(categories)
        ]
        if len(set(category_text)) != len(category_text):
            raise SerializationError("xychart x_axis.categories must be unique")
        x_spec = f"[{', '.join(f'{chr(34)}{item}{chr(34)}' for item in category_text)}]"
    else:
        x_min, x_max, minimum_text, maximum_text = _axis_bounds(x_axis, field="xychart x_axis")
        x_spec = f"{minimum_text} --> {maximum_text}"
    y_min, y_max, y_minimum_text, y_maximum_text = _axis_bounds(
        ir.get("y_axis"),
        field="xychart y_axis",
    )
    x_label = x_axis.get("label")
    y_axis = ir["y_axis"]
    y_label = y_axis.get("label")
    lines = [
        "xychart-beta",
        *_accessibility(ir, "xychart", experimental=experimental),
        *_title_line(ir),
    ]
    x_label_prefix = f'"{_text(x_label)}" ' if x_label else ""
    y_label_prefix = f'"{_text(y_label)}" ' if y_label else ""
    lines.append(f"    x-axis {x_label_prefix}{x_spec}")
    lines.append(f"    y-axis {y_label_prefix}{y_minimum_text} --> {y_maximum_text}")

    series_items = ir.get("series")
    if not isinstance(series_items, list) or not series_items:
        raise SerializationError("xychart IR requires a non-empty series list")
    for index, series in enumerate(series_items, start=1):
        if not isinstance(series, dict):
            raise SerializationError("xychart series must be objects")
        if series.get("label") is not None or series.get("name") is not None:
            raise SerializationError("Mermaid 11.16 xychart has no strict-safe series-label syntax")
        kind = str(series.get("kind") or "").lower()
        if kind not in {"line", "bar"}:
            raise SerializationError(f"xychart series {index} kind must be line or bar")
        values = _xy_series_values(
            series,
            index=index,
            x_min=x_min,
            x_max=x_max,
            y_min=y_min,
            y_max=y_max,
        )
        if categories is not None and len(values) != len(categories):
            raise SerializationError(
                f"xychart series {index} has {len(values)} values for {len(categories)} categories"
            )
        lines.append(f"    {kind} [{', '.join(values)}]")
    return "\n".join(lines) + "\n", "xychart", None


def _quadrant_labels(value: Any) -> dict[int, str]:
    if value is None:
        return {}
    if isinstance(value, list):
        if len(value) != 4:
            raise SerializationError("quadrant labels list must contain exactly four entries")
        return {
            index: _required_text(label, field=f"quadrant label {index}")
            for index, label in enumerate(value, start=1)
        }
    if not isinstance(value, dict):
        raise SerializationError("quadrant labels must be a four-entry list or object")
    labels: dict[int, str] = {}
    for raw_key, raw_label in value.items():
        key = str(raw_key).lower().removeprefix("quadrant-")
        if key not in {"1", "2", "3", "4"}:
            raise SerializationError(f"unsupported quadrant label key {raw_key!r}")
        number = int(key)
        if number in labels:
            raise SerializationError(f"duplicate quadrant label alias for quadrant-{number}")
        labels[number] = _required_text(raw_label, field=f"quadrant label {number}")
    return labels


def serialize_quadrant(ir: dict[str, Any], *, experimental: bool = False) -> ChartCoreSerialization:
    """Serialize normalized positioned points to native ``quadrantChart``."""

    axis_lines: list[str] = []
    for name in ("x_axis", "y_axis"):
        axis = ir.get(name)
        if not isinstance(axis, dict):
            raise SerializationError(f"quadrant {name} must be an object")
        low = _required_text(axis.get("low"), field=f"quadrant {name}.low")
        high = _required_text(axis.get("high"), field=f"quadrant {name}.high")
        axis_lines.append(f'    {name[0]}-axis "{low}" --> "{high}"')

    points = ir.get("points")
    if not isinstance(points, list) or not points:
        raise SerializationError("quadrant IR requires a non-empty points list")
    point_lines: list[str] = []
    labels: set[str] = set()
    for index, point in enumerate(points, start=1):
        if not isinstance(point, dict):
            raise SerializationError("quadrant points must be objects")
        label = _required_text(point.get("label"), field=f"quadrant point {index}.label")
        if label in labels:
            raise SerializationError(f"quadrant point labels must be unique: {label!r}")
        labels.add(label)
        x, x_text = _number(point.get("x"), field=f"quadrant point {index}.x")
        y, y_text = _number(point.get("y"), field=f"quadrant point {index}.y")
        if not Decimal(0) <= x <= Decimal(1) or not Decimal(0) <= y <= Decimal(1):
            raise SerializationError(
                f"quadrant point {index} coordinates must be normalized to [0, 1]"
            )
        point_lines.append(f'    "{label}": [{x_text}, {y_text}]')

    lines = [
        "quadrantChart",
        *_accessibility(ir, "quadrant", experimental=experimental),
        *_title_line(ir),
        *axis_lines,
    ]
    for number, label in sorted(_quadrant_labels(ir.get("quadrants")).items()):
        lines.append(f'    quadrant-{number} "{label}"')
    lines.extend(point_lines)
    return "\n".join(lines) + "\n", "quadrant", None


CHART_CORE_SERIALIZERS: dict[str, Callable[..., ChartCoreSerialization]] = {
    "pie": serialize_pie,
    "xychart": serialize_xychart,
    "quadrant": serialize_quadrant,
}


def serialize_chart_core(
    diagram_type: str, ir: dict[str, Any], *, experimental: bool = False
) -> ChartCoreSerialization:
    """Serialize a core chart IR and disclose the emitted Mermaid grammar."""

    serializer = CHART_CORE_SERIALIZERS.get(diagram_type)
    if serializer is None:
        raise SerializationError(f"no core chart typed serializer for {diagram_type}")
    return serializer(ir, experimental=experimental)
