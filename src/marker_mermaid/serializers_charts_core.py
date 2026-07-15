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
MAX_XY_NATIVE_SERIES = 10
MAX_XY_FLOWCHART_POINTS = 256
MAX_XY_OUTPUT_CHARS = 50_000
MAX_XY_OUTPUT_LINES = 5_000

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
XY_FALLBACK_WARNING = (
    "XY Chart was emitted as an exact-value Flowchart because Mermaid 11.16 would "
    "drop, hide, reposition, or rewrite one or more data points."
)
XY_RUNTIME_FALLBACK_WARNING = (
    "CandidateValidator rejected native XY Chart; exact axis and point cells were "
    "re-emitted as a portable Flowchart in the same candidate slot."
)
XY_NATIVE_TEXT_COMPATIBILITY_WARNING = (
    "XY Chart canvas text used visible compatibility substitutions; semantic text remains "
    "in typed IR and review metadata."
)
XY_FALLBACK_TEXT_COMPATIBILITY_WARNING = (
    "XY Chart Flowchart text used visible compatibility substitutions; semantic text remains "
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


@dataclass(frozen=True, slots=True)
class XYCategoryPlan:
    """One categorical x-axis tick in the pinned Mermaid terminal."""

    source_index: int
    scene_id: str
    label: str
    native_source_label: str
    native_canvas_label: str
    normalized_point: tuple[float, float]
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class XYAxisPlan:
    """One exact source axis and its native/fallback text projections."""

    source_record: Mapping[str, Any]
    scene_id: str
    label: str | None
    native_source_label: str | None
    native_canvas_label: str | None
    fallback_source_label: str
    fallback_canvas_label: str
    categories: tuple[XYCategoryPlan, ...]
    minimum: Decimal | None
    maximum: Decimal | None
    minimum_text: str | None
    maximum_text: str | None
    native_minimum: float | None
    native_maximum: float | None
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class XYPointPlan:
    """One exact source value and its terminal geometry/cell projection."""

    source_record: Mapping[str, Any]
    scene_id: str
    x: Decimal | None
    x_text: str | None
    y: Decimal
    y_text: str
    native_x: float | None
    native_y: float | None
    normalized_point: tuple[float, float] | None
    fallback_source_label: str
    fallback_canvas_label: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class XYSeriesPlan:
    """One line/bar series with its ordered emitted points."""

    source_record: Mapping[str, Any]
    emitted_id: str
    kind: str
    points: tuple[XYPointPlan, ...]
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class XYPlan:
    """Bounded terminal plan shared by XY serialization, Scene, and OCR."""

    x_axis: XYAxisPlan
    y_axis: XYAxisPlan
    series: tuple[XYSeriesPlan, ...]
    total_points: int
    native_supported: bool
    flowchart_supported: bool
    native_limitations: tuple[str, ...]
    semantic_title: str | None
    native_source_title: str | None
    native_canvas_title: str | None
    fallback_source_title: str | None
    fallback_canvas_title: str | None
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


def _xy_neutralize_source_text(text: str) -> str:
    """Break active source tokens while retaining visually equivalent text."""

    source = (
        text.replace("%%", f"%{_ZERO_WIDTH_SPACE}%")
        .replace("//", f"/{_ZERO_WIDTH_SPACE}/")
        .replace(";", f";{_ZERO_WIDTH_SPACE}")
        .replace("<", f"<{_ZERO_WIDTH_SPACE}")
    )
    source = _PIE_CSS_IMPORT.sub(
        lambda match: f"{match.group(0)[:2]}{_ZERO_WIDTH_SPACE}{match.group(0)[2:]}",
        source,
    )
    source = _PIE_DANGEROUS_SCHEME.sub(
        lambda match: f"{match.group(0)[:-1]}{_ZERO_WIDTH_SPACE}:",
        source,
    )
    source = _PIE_REMOTE_ICON.sub(
        lambda match: f"{match.group(0)[0]}{_ZERO_WIDTH_SPACE}{match.group(0)[1:]}",
        source,
    )
    source = _PIE_ACTIVE_CALLBACK.sub(
        lambda match: f"{match.group(0)[0]}{_ZERO_WIDTH_SPACE}{match.group(0)[1:]}",
        source,
    )
    return source.replace("&", f"&{_ZERO_WIDTH_SPACE}").replace(
        "#", f"#{_ZERO_WIDTH_SPACE}"
    )


def validate_xychart_explicit_metadata(ir: Mapping[str, Any]) -> None:
    """Reject explicit XY metadata before accessibility enrichment can stringify it."""

    for field in ("title", "description", "acc_title", "acc_description"):
        value = ir.get(field)
        if value is not None and type(value) is not str:
            raise SerializationError(f"xychart {field} must be text when provided")


def plan_xychart_records(ir: Mapping[str, Any]) -> XYPlan:
    """Validate XY records and freeze the exact native/fallback terminal plan."""

    validate_xychart_explicit_metadata(ir)

    x_axis = ir.get("x_axis")
    y_axis = ir.get("y_axis")
    if not isinstance(x_axis, dict):
        raise SerializationError("xychart x_axis must be an object")
    if not isinstance(y_axis, dict):
        raise SerializationError("xychart y_axis must be an object")
    if x_axis is y_axis:
        raise SerializationError("xychart axes cannot reuse one object")

    seen_records = {id(x_axis), id(y_axis)}
    translation = str.maketrans(
        {'"': "″", "\\": "∖", "<": "＜", ">": "＞", "#": "＃"}
    )
    native_limitations: list[str] = []
    native_compatibility_substitutions = False
    fallback_compatibility_substitutions = False

    axis_evidence: dict[str, tuple[str, ...]] = {}
    for axis_name, axis in (("x", x_axis), ("y", y_axis)):
        raw_evidence_ids = axis.get("evidence_ids")
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
                evidence_ids = tuple(dict.fromkeys(raw_evidence_ids))
        else:
            evidence_ids = ()
        axis_evidence[axis_name] = evidence_ids

    projected_axis_labels: dict[str, tuple[str | None, str | None, str | None]] = {}
    for axis_name, axis in (("x", x_axis), ("y", y_axis)):
        raw_label = axis.get("label")
        if raw_label is None or raw_label == "":
            projected_axis_labels[axis_name] = (None, None, None)
            continue
        if type(raw_label) is not str:
            raise SerializationError(f"xychart {axis_name}_axis.label must be text")
        label = " ".join(raw_label.split())
        if not label or len(label) > MAX_TEXT_CHARS:
            raise SerializationError(
                f"xychart {axis_name}_axis.label requires bounded non-empty text"
            )
        if any(
            unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"}
            for character in label
        ):
            raise SerializationError(f"xychart {axis_name}_axis.label contains unsupported text")
        try:
            label.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise SerializationError(
                f"xychart {axis_name}_axis.label is not valid UTF-8"
            ) from exc
        canvas_label = label.translate(translation)
        source_label = _xy_neutralize_source_text(canvas_label)
        native_compatibility_substitutions |= canvas_label != raw_label
        fallback_compatibility_substitutions |= canvas_label != raw_label
        projected_axis_labels[axis_name] = (label, source_label, canvas_label)

    categories_value = x_axis.get("categories")
    categories: list[XYCategoryPlan] = []
    x_minimum: Decimal | None = None
    x_maximum: Decimal | None = None
    x_minimum_text: str | None = None
    x_maximum_text: str | None = None
    native_x_minimum: float | None = None
    native_x_maximum: float | None = None
    if categories_value is not None:
        if "min" in x_axis or "max" in x_axis:
            raise SerializationError("xychart x_axis cannot mix categories with numeric bounds")
        if not isinstance(categories_value, list) or not categories_value:
            raise SerializationError("xychart x_axis.categories must be a non-empty list")
        if len(categories_value) > MAX_XY_FLOWCHART_POINTS:
            raise SerializationError(
                f"XY Chart exceeds the {MAX_XY_FLOWCHART_POINTS}-point runtime limit"
            )
        labels: set[str] = set()
        native_canvas_labels: set[str] = set()
        category_count = len(categories_value)
        for offset, raw_category in enumerate(categories_value):
            if type(raw_category) is not str:
                raise SerializationError(
                    f"xychart x_axis.categories[{offset}] requires non-empty text evidence"
                )
            category = " ".join(raw_category.split())
            if not category or len(category) > MAX_TEXT_CHARS:
                raise SerializationError(
                    f"xychart x_axis.categories[{offset}] requires bounded non-empty text"
                )
            if any(
                unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"}
                for character in category
            ):
                raise SerializationError(
                    f"xychart x_axis.categories[{offset}] contains unsupported text"
                )
            try:
                category.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise SerializationError(
                    f"xychart x_axis.categories[{offset}] is not valid UTF-8"
                ) from exc
            if category in labels:
                raise SerializationError("xychart x_axis.categories must be unique")
            labels.add(category)
            canvas_category = category.translate(translation)
            if canvas_category in native_canvas_labels:
                native_limitations.append(
                    "x-axis categories collide after native canvas compatibility substitution"
                )
            native_canvas_labels.add(canvas_category)
            source_category = _xy_neutralize_source_text(canvas_category)
            native_compatibility_substitutions |= canvas_category != raw_category
            fallback_compatibility_substitutions |= canvas_category != raw_category
            normalized_x = 0.5 if category_count == 1 else offset / (category_count - 1)
            categories.append(
                XYCategoryPlan(
                    source_index=offset,
                    scene_id=f"xy_category_{offset + 1}",
                    label=category,
                    native_source_label=source_category,
                    native_canvas_label=canvas_category,
                    normalized_point=(normalized_x, 1.0),
                    evidence_ids=axis_evidence["x"],
                )
            )
    else:
        x_minimum, x_maximum, x_minimum_text, x_maximum_text = _axis_bounds(
            x_axis,
            field="xychart x_axis",
        )
        try:
            native_x_minimum = float(x_minimum_text)
            native_x_maximum = float(x_maximum_text)
        except (OverflowError, ValueError):
            native_x_minimum = None
            native_x_maximum = None
        if native_x_minimum is None or native_x_maximum is None or any(
            not math.isfinite(value)
            or (decimal != 0 and value == 0)
            or (value != 0 and abs(value) < sys.float_info.min)
            or Decimal(str(value)) != decimal
            for value, decimal in (
                (native_x_minimum, x_minimum),
                (native_x_maximum, x_maximum),
            )
        ):
            native_limitations.append(
                "x-axis bounds are not zero-or-normal binary64 round-trip safe"
            )
            native_x_minimum = None
            native_x_maximum = None
        elif (
            not math.isfinite(native_x_maximum - native_x_minimum)
            or native_x_maximum - native_x_minimum <= 0
            or native_x_maximum - native_x_minimum < sys.float_info.min
        ):
            native_limitations.append(
                "the pinned renderer requires a positive normal finite x-axis span"
            )
            native_x_minimum = None
            native_x_maximum = None

    y_minimum, y_maximum, y_minimum_text, y_maximum_text = _axis_bounds(
        y_axis,
        field="xychart y_axis",
    )
    try:
        native_y_minimum = float(y_minimum_text)
        native_y_maximum = float(y_maximum_text)
    except (OverflowError, ValueError):
        native_y_minimum = None
        native_y_maximum = None
    if native_y_minimum is None or native_y_maximum is None or any(
        not math.isfinite(value)
        or (decimal != 0 and value == 0)
        or (value != 0 and abs(value) < sys.float_info.min)
        or Decimal(str(value)) != decimal
        for value, decimal in (
            (native_y_minimum, y_minimum),
            (native_y_maximum, y_maximum),
        )
    ):
        native_limitations.append("y-axis bounds are not zero-or-normal binary64 round-trip safe")
        native_y_minimum = None
        native_y_maximum = None
    elif (
        not math.isfinite(native_y_maximum - native_y_minimum)
        or native_y_maximum - native_y_minimum <= 0
        or native_y_maximum - native_y_minimum < sys.float_info.min
    ):
        native_limitations.append(
            "the pinned renderer requires a positive normal finite y-axis span"
        )
        native_y_minimum = None
        native_y_maximum = None

    semantic_title: str | None = None
    native_source_title: str | None = None
    native_canvas_title: str | None = None
    raw_title = ir.get("title")
    if raw_title is not None and raw_title != "":
        semantic_title = " ".join(raw_title.split())
        if not semantic_title or len(semantic_title) > MAX_TEXT_CHARS:
            raise SerializationError("xychart title must be bounded non-empty text")
        if any(
            unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"}
            for character in semantic_title
        ):
            raise SerializationError("xychart title contains unsupported text")
        try:
            semantic_title.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise SerializationError("xychart title is not valid UTF-8") from exc
        native_canvas_title = semantic_title.translate(translation)
        native_source_title = _xy_neutralize_source_text(native_canvas_title)
        native_compatibility_substitutions |= native_canvas_title != raw_title
        fallback_compatibility_substitutions |= native_canvas_title != raw_title

    x_label, x_source_label, x_canvas_label = projected_axis_labels["x"]
    y_label, y_source_label, y_canvas_label = projected_axis_labels["y"]
    if categories:
        x_fallback_canvas = "X axis" + (
            f" — {x_canvas_label}" if x_canvas_label is not None else ""
        )
    else:
        x_fallback_canvas = "X axis" + (
            f" — {x_canvas_label}" if x_canvas_label is not None else ""
        ) + f": {x_minimum_text} to {x_maximum_text}"
    y_fallback_canvas = "Y axis" + (
        f" — {y_canvas_label}" if y_canvas_label is not None else ""
    ) + f": {y_minimum_text} to {y_maximum_text}"
    x_axis_plan = XYAxisPlan(
        source_record=x_axis,
        scene_id="xy_x_axis",
        label=x_label,
        native_source_label=x_source_label,
        native_canvas_label=x_canvas_label,
        fallback_source_label=_xy_neutralize_source_text(x_fallback_canvas),
        fallback_canvas_label=x_fallback_canvas,
        categories=tuple(categories),
        minimum=x_minimum,
        maximum=x_maximum,
        minimum_text=x_minimum_text,
        maximum_text=x_maximum_text,
        native_minimum=native_x_minimum,
        native_maximum=native_x_maximum,
        evidence_ids=axis_evidence["x"],
    )
    y_axis_plan = XYAxisPlan(
        source_record=y_axis,
        scene_id="xy_y_axis",
        label=y_label,
        native_source_label=y_source_label,
        native_canvas_label=y_canvas_label,
        fallback_source_label=_xy_neutralize_source_text(y_fallback_canvas),
        fallback_canvas_label=y_fallback_canvas,
        categories=(),
        minimum=y_minimum,
        maximum=y_maximum,
        minimum_text=y_minimum_text,
        maximum_text=y_maximum_text,
        native_minimum=native_y_minimum,
        native_maximum=native_y_maximum,
        evidence_ids=axis_evidence["y"],
    )

    series_items = ir.get("series")
    if not isinstance(series_items, list) or not series_items:
        raise SerializationError("xychart IR requires a non-empty series list")
    if len(series_items) > MAX_XY_FLOWCHART_POINTS:
        raise SerializationError(
            f"XY Chart exceeds the {MAX_XY_FLOWCHART_POINTS}-point runtime limit"
        )
    if len(series_items) > MAX_XY_NATIVE_SERIES:
        native_limitations.append(
            f"more than {MAX_XY_NATIVE_SERIES} series repeat the pinned color palette"
        )

    planned_series: list[XYSeriesPlan] = []
    total_points = 0
    for series_index, series in enumerate(series_items, start=1):
        if not isinstance(series, dict):
            raise SerializationError("xychart series must be objects")
        if id(series) in seen_records:
            raise SerializationError("xychart axes, series, and points cannot reuse one object")
        seen_records.add(id(series))
        if series.get("label") is not None or series.get("name") is not None:
            raise SerializationError("Mermaid 11.16 xychart has no strict-safe series-label syntax")
        raw_kind = series.get("kind")
        if type(raw_kind) is not str or raw_kind.casefold() not in {"line", "bar"}:
            raise SerializationError(f"xychart series {series_index} kind must be line or bar")
        kind = raw_kind.casefold()
        raw_series_evidence = series.get("evidence_ids")
        if raw_series_evidence is None:
            series_evidence: tuple[str, ...] = ()
        elif (
            isinstance(raw_series_evidence, list)
            and len(raw_series_evidence) <= MAX_EVIDENCE_REFS
            and all(
                type(evidence_id) is str
                and bool(evidence_id)
                and len(evidence_id) <= MAX_ID_CHARS
                for evidence_id in raw_series_evidence
            )
        ):
            try:
                for evidence_id in raw_series_evidence:
                    evidence_id.encode("utf-8")
            except UnicodeEncodeError:
                series_evidence = ()
            else:
                series_evidence = tuple(dict.fromkeys(raw_series_evidence))
        else:
            series_evidence = ()

        has_values = "values" in series
        has_points = "points" in series
        if has_values == has_points:
            raise SerializationError(
                f"xychart series {series_index} requires exactly one of values or points"
            )
        source_values = series.get("values") if has_values else series.get("points")
        if not isinstance(source_values, list) or not source_values:
            field = "values" if has_values else "points"
            raise SerializationError(
                f"xychart series {series_index}.{field} must be a non-empty list"
            )
        if has_points and len(source_values) < 2:
            raise SerializationError(
                f"xychart series {series_index}.points requires at least two explicit coordinates"
            )
        if categories and has_points:
            raise SerializationError("xychart points require a numeric x_axis")
        if categories and len(source_values) != len(categories):
            raise SerializationError(
                f"xychart series {series_index} has {len(source_values)} values for "
                f"{len(categories)} categories"
            )
        total_points += len(source_values)
        if total_points > MAX_XY_FLOWCHART_POINTS:
            raise SerializationError(
                f"XY Chart exceeds the {MAX_XY_FLOWCHART_POINTS}-point runtime limit"
            )
        if kind == "line" and len(source_values) < 2:
            native_limitations.append(
                f"series {series_index} is a one-point line with no visible native segment"
            )

        native_x_positions: list[float | None]
        if categories:
            native_x_positions = [category.normalized_point[0] for category in categories]
        elif native_x_minimum is None or native_x_maximum is None:
            native_x_positions = [None] * len(source_values)
        elif len(source_values) == 1:
            native_x_positions = [native_x_minimum]
        else:
            native_step = (native_x_maximum - native_x_minimum) / (len(source_values) - 1)
            generated_positions: list[float] = []
            current_x = native_x_minimum
            next_x = current_x
            grid_safe = (
                math.isfinite(native_step)
                and native_step >= sys.float_info.min
            )
            if grid_safe:
                for _ in range(len(source_values) + 1):
                    if current_x > native_x_maximum:
                        break
                    generated_positions.append(current_x)
                    next_x = current_x + native_step
                    if not math.isfinite(next_x) or next_x <= current_x:
                        grid_safe = False
                        break
                    current_x = next_x
            if (
                not grid_safe
                or len(generated_positions) != len(source_values)
                or generated_positions[0] != native_x_minimum
                or generated_positions[-1] != native_x_maximum
                or next_x <= native_x_maximum
            ):
                native_limitations.append(
                    f"series {series_index} does not make bounded exact progress in Mermaid's "
                    "numeric x-axis loop"
                )
                native_x_positions = [None] * len(source_values)
            else:
                native_x_positions = list(generated_positions)

        semantic_x_step = None
        if not categories and len(source_values) > 1:
            assert x_minimum is not None and x_maximum is not None
            semantic_x_step = (x_maximum - x_minimum) / (len(source_values) - 1)
        planned_points: list[XYPointPlan] = []
        for point_index, source_value in enumerate(source_values, start=1):
            x: Decimal | None = None
            x_text: str | None = None
            point_record: Mapping[str, Any] = series
            point_evidence = series_evidence
            if has_points:
                if not isinstance(source_value, dict):
                    raise SerializationError(
                        f"xychart series {series_index}.points must be objects"
                    )
                if id(source_value) in seen_records:
                    raise SerializationError(
                        "xychart axes, series, and points cannot reuse one object"
                    )
                seen_records.add(id(source_value))
                point_record = source_value
                x, x_text = _number(
                    source_value.get("x"),
                    field=f"xychart series {series_index}.points[{point_index - 1}].x",
                )
                y, y_text = _number(
                    source_value.get("y"),
                    field=f"xychart series {series_index}.points[{point_index - 1}].y",
                )
                raw_point_evidence = source_value.get("evidence_ids")
                if raw_point_evidence is None:
                    point_evidence = ()
                elif (
                    isinstance(raw_point_evidence, list)
                    and len(raw_point_evidence) <= MAX_EVIDENCE_REFS
                    and all(
                        type(evidence_id) is str
                        and bool(evidence_id)
                        and len(evidence_id) <= MAX_ID_CHARS
                        for evidence_id in raw_point_evidence
                    )
                ):
                    try:
                        for evidence_id in raw_point_evidence:
                            evidence_id.encode("utf-8")
                    except UnicodeEncodeError:
                        point_evidence = ()
                    else:
                        point_evidence = tuple(dict.fromkeys(raw_point_evidence))
                else:
                    point_evidence = ()
                assert semantic_x_step is not None and x_minimum is not None
                expected_x = x_minimum + semantic_x_step * (point_index - 1)
                if x != expected_x:
                    native_limitations.append(
                        f"series {series_index} point {point_index} is not on the exact "
                        "uniform source grid"
                    )
            else:
                y, y_text = _number(
                    source_value,
                    field=f"xychart series {series_index}.values[{point_index - 1}]",
                )
            if not y_minimum <= y <= y_maximum:
                field = (
                    f"points[{point_index - 1}].y"
                    if has_points
                    else f"values[{point_index - 1}]"
                )
                raise SerializationError(
                    f"xychart series {series_index}.{field} must be within y_axis bounds"
                )
            if kind == "bar" and y == y_minimum:
                native_limitations.append(
                    f"series {series_index} point {point_index} is a zero-height bar at "
                    "the y-axis minimum"
                )

            try:
                native_y = float(y_text)
            except (OverflowError, ValueError):
                native_y = None
            if native_y is not None and (
                not math.isfinite(native_y)
                or (y != 0 and native_y == 0)
                or (native_y != 0 and abs(native_y) < sys.float_info.min)
                or Decimal(str(native_y)) != y
            ):
                native_y = None
            if native_y is None:
                native_limitations.append(
                    f"series {series_index} point {point_index} y value is not "
                    "zero-or-normal binary64 round-trip safe"
                )

            native_x = native_x_positions[point_index - 1]
            if has_points:
                assert x is not None and x_text is not None
                try:
                    source_native_x = float(x_text)
                except (OverflowError, ValueError):
                    source_native_x = None
                if source_native_x is not None and (
                    not math.isfinite(source_native_x)
                    or (x != 0 and source_native_x == 0)
                    or (source_native_x != 0 and abs(source_native_x) < sys.float_info.min)
                    or Decimal(str(source_native_x)) != x
                ):
                    source_native_x = None
                if source_native_x is None or native_x is None or source_native_x != native_x:
                    native_limitations.append(
                        f"series {series_index} point {point_index} x value would be "
                        "rewritten by Mermaid's numeric grid"
                    )

            normalized_point: tuple[float, float] | None = None
            if native_x is not None and native_y is not None and native_y_minimum is not None:
                assert native_y_maximum is not None
                normalized_x = (
                    native_x
                    if categories
                    else (native_x - native_x_minimum) / (native_x_maximum - native_x_minimum)
                )
                normalized_y = 1 - (
                    (native_y - native_y_minimum) / (native_y_maximum - native_y_minimum)
                )
                if all(math.isfinite(value) for value in (normalized_x, normalized_y)):
                    normalized_point = (normalized_x, normalized_y)
                else:
                    native_limitations.append(
                        f"series {series_index} point {point_index} creates non-finite geometry"
                    )

            if categories:
                category = categories[point_index - 1]
                fallback_semantic = f"{kind} · {category.label}: value {y_text}"
            elif has_points:
                fallback_semantic = f"{kind} · x {x_text}, y {y_text}"
            else:
                fallback_semantic = f"{kind} · value {y_text}"
            fallback_canvas = fallback_semantic.translate(translation)
            fallback_source = _xy_neutralize_source_text(fallback_canvas)
            fallback_compatibility_substitutions |= fallback_canvas != fallback_semantic
            planned_points.append(
                XYPointPlan(
                    source_record=point_record,
                    scene_id=f"xy_series_{series_index}_point_{point_index}",
                    x=x,
                    x_text=x_text,
                    y=y,
                    y_text=y_text,
                    native_x=native_x,
                    native_y=native_y,
                    normalized_point=normalized_point,
                    fallback_source_label=fallback_source,
                    fallback_canvas_label=fallback_canvas,
                    evidence_ids=point_evidence,
                )
            )
        planned_series.append(
            XYSeriesPlan(
                source_record=series,
                emitted_id=f"xy_series_{series_index}",
                kind=kind,
                points=tuple(planned_points),
                evidence_ids=series_evidence,
            )
        )

    bar_series_count = sum(series.kind == "bar" for series in planned_series)
    if bar_series_count > 1:
        native_limitations.append(
            "multiple bar series share identical native x positions and can occlude each other"
        )
    line_sequences: set[tuple[str, ...]] = set()
    for series in planned_series:
        if series.kind != "line":
            continue
        sequence = tuple(point.y_text for point in series.points)
        if sequence in line_sequences:
            native_limitations.append(
                "identical line series share one native path and occlude each other"
            )
            break
        line_sequences.add(sequence)

    native_supported = not native_limitations and all(
        point.normalized_point is not None
        for series in planned_series
        for point in series.points
    )
    fallback_line_count = 5 + len(categories) + total_points + int(semantic_title is not None)
    return XYPlan(
        x_axis=x_axis_plan,
        y_axis=y_axis_plan,
        series=tuple(planned_series),
        total_points=total_points,
        native_supported=native_supported,
        flowchart_supported=(
            total_points <= MAX_XY_FLOWCHART_POINTS
            and fallback_line_count + 1 <= MAX_XY_OUTPUT_LINES
        ),
        native_limitations=tuple(dict.fromkeys(native_limitations)),
        semantic_title=semantic_title,
        native_source_title=native_source_title,
        native_canvas_title=native_canvas_title,
        fallback_source_title=native_source_title,
        fallback_canvas_title=native_canvas_title,
        native_compatibility_substitutions=native_compatibility_substitutions,
        fallback_compatibility_substitutions=fallback_compatibility_substitutions,
    )


def serialize_xychart(
    ir: dict[str, Any],
    *,
    experimental: bool = False,
    native_runtime_valid: bool = True,
) -> ChartCoreSerialization:
    """Serialize a terminal-faithful native XY chart or exact disconnected fallback."""

    if not isinstance(native_runtime_valid, bool):
        raise SerializationError("native_runtime_valid must be a boolean")
    plan = plan_xychart_records(ir)
    accessibility = resolve_accessibility(ir, "xychart", experimental=experimental)
    accessibility_source: list[str] = []
    for value in (accessibility.title, accessibility.description):
        if not value or len(value) > MAX_TEXT_CHARS:
            raise SerializationError("XY Chart accessibility text must be bounded and non-empty")
        if any(
            unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"}
            for character in value
        ):
            raise SerializationError("XY Chart accessibility text contains unsupported text")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise SerializationError("XY Chart accessibility text is not valid UTF-8") from exc
        accessibility_source.append(_xy_neutralize_source_text(value))

    use_fallback = not native_runtime_valid or not plan.native_supported
    fallback_reason: str | None = None
    if use_fallback:
        if not plan.flowchart_supported:
            raise SerializationError(
                f"XY Chart Flowchart fallback exceeds the "
                f"{MAX_XY_FLOWCHART_POINTS}-point runtime limit"
            )
        lines = [
            "flowchart TB",
            f"    accTitle: {accessibility_source[0]}",
            f"    accDescr: {accessibility_source[1]} "
            "This XY Chart reconstruction uses an exact-value Flowchart fallback.",
        ]
        if plan.fallback_source_title is not None:
            lines.append(f'    xy_title["{plan.fallback_source_title}"]')
        lines.extend(
            (
                f'    {plan.x_axis.scene_id}["{plan.x_axis.fallback_source_label}"]',
                f'    {plan.y_axis.scene_id}["{plan.y_axis.fallback_source_label}"]',
            )
        )
        lines.extend(
            f'    {category.scene_id}["{category.native_source_label}"]'
            for category in plan.x_axis.categories
        )
        lines.extend(
            f'    {point.scene_id}["{point.fallback_source_label}"]'
            for series in plan.series
            for point in series.points
        )
        fallback_reason = (
            XY_RUNTIME_FALLBACK_WARNING
            if not native_runtime_valid
            else f"{XY_FALLBACK_WARNING} {'; '.join(plan.native_limitations)}"
        )
        emitted_type = "flowchart"
    else:
        lines = [
            "xychart-beta",
            f"    accTitle: {accessibility_source[0]}",
            f"    accDescr: {accessibility_source[1]}",
        ]
        if plan.native_source_title is not None:
            lines.append(f'    title "{plan.native_source_title}"')
        x_label_prefix = (
            f'"{plan.x_axis.native_source_label}" '
            if plan.x_axis.native_source_label is not None
            else ""
        )
        if plan.x_axis.categories:
            x_spec = "[" + ", ".join(
                f'"{category.native_source_label}"' for category in plan.x_axis.categories
            ) + "]"
        else:
            x_spec = f"{plan.x_axis.minimum_text} --> {plan.x_axis.maximum_text}"
        y_label_prefix = (
            f'"{plan.y_axis.native_source_label}" '
            if plan.y_axis.native_source_label is not None
            else ""
        )
        lines.extend(
            (
                f"    x-axis {x_label_prefix}{x_spec}",
                f"    y-axis {y_label_prefix}{plan.y_axis.minimum_text} --> "
                f"{plan.y_axis.maximum_text}",
            )
        )
        lines.extend(
            f"    {series.kind} [{', '.join(point.y_text for point in series.points)}]"
            for series in plan.series
        )
        emitted_type = "xychart"

    code = "\n".join(lines) + "\n"
    if code.count("\n") + 1 > MAX_XY_OUTPUT_LINES:
        raise SerializationError(
            f"XY Chart output exceeds source-line limit of {MAX_XY_OUTPUT_LINES}"
        )
    try:
        utf16_code_units = len(code.encode("utf-16-le")) // 2
    except UnicodeEncodeError as exc:
        raise SerializationError("XY Chart output is not valid UTF-16") from exc
    if utf16_code_units > MAX_XY_OUTPUT_CHARS:
        raise SerializationError(
            f"XY Chart output exceeds UTF-16 source-character limit of {MAX_XY_OUTPUT_CHARS}"
        )
    report = MermaidSecurityScanner(SecurityProfile.STRICT).scan(code)
    if not report.safe:
        rules = ", ".join(sorted({finding.rule for finding in report.findings}))
        raise SerializationError(f"XY Chart text violates the strict security profile: {rules}")
    return code, emitted_type, fallback_reason


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
