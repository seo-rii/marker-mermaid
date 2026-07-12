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
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Any, TypeAlias

from marker_mermaid.serializers import SerializationError

ChartCoreSerialization: TypeAlias = tuple[str, str, str | None]


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


def _accessibility(ir: dict[str, Any], *, experimental: bool) -> list[str]:
    title = ir.get("acc_title") or ir.get("title")
    description = ir.get("acc_description") or ir.get("description")
    if experimental:
        warning = "This reconstruction is experimental and requires review."
        description = f"{description} {warning}" if description else warning
    lines: list[str] = []
    if title:
        lines.append(f"    accTitle: {_text(title)}")
    if description:
        lines.append(f"    accDescr: {_text(description)}")
    return lines


def _number(value: Any, *, field: str) -> tuple[Decimal, str]:
    """Validate and format a finite JSON-style number without float invention."""

    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        raise SerializationError(f"{field} requires an explicit numeric value")
    if isinstance(value, float) and not math.isfinite(value):
        raise SerializationError(f"{field} requires a finite numeric value")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise SerializationError(f"{field} requires a finite numeric value") from exc
    if not decimal.is_finite():
        raise SerializationError(f"{field} requires a finite numeric value")
    if decimal == 0:
        return Decimal(0), "0"
    rendered = format(decimal, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
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


def serialize_pie(ir: dict[str, Any], *, experimental: bool = False) -> ChartCoreSerialization:
    """Serialize explicit label/value slices to native Mermaid ``pie`` syntax."""

    slices = ir.get("slices")
    if not isinstance(slices, list) or not slices:
        raise SerializationError("pie IR requires a non-empty slices list")
    show_data = ir.get("show_data", False)
    if not isinstance(show_data, bool):
        raise SerializationError("pie show_data must be a boolean")
    lines = ["pie showData" if show_data else "pie"]
    lines.extend(_accessibility(ir, experimental=experimental))
    lines.extend(_title_line(ir))
    labels: set[str] = set()
    total = Decimal(0)
    for index, item in enumerate(slices, start=1):
        if not isinstance(item, dict):
            raise SerializationError("pie slices must be objects")
        label = _required_text(item.get("label"), field=f"pie slice {index}.label")
        if label in labels:
            raise SerializationError(f"pie slice labels must be unique: {label!r}")
        labels.add(label)
        value, value_text = _number(item.get("value"), field=f"pie slice {index}.value")
        if value < 0:
            raise SerializationError(f"pie slice {index}.value cannot be negative")
        total += value
        lines.append(f'    "{label}" : {value_text}')
    if total <= 0:
        raise SerializationError("pie slices require a positive total")
    return "\n".join(lines) + "\n", "pie", None


def _xy_series_values(
    series: dict[str, Any],
    *,
    index: int,
    x_min: Decimal | None,
    x_max: Decimal | None,
) -> list[str]:
    has_values = "values" in series
    has_points = "points" in series
    if has_values == has_points:
        raise SerializationError(f"xychart series {index} requires exactly one of values or points")
    if has_values:
        values = series["values"]
        if not isinstance(values, list) or not values:
            raise SerializationError(f"xychart series {index}.values must be a non-empty list")
        return [
            _number(value, field=f"xychart series {index}.values[{offset}]")[1]
            for offset, value in enumerate(values)
        ]

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
        _, y_text = _number(point.get("y"), field=f"xychart series {index}.points[{offset}].y")
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
    _, _, y_minimum_text, y_maximum_text = _axis_bounds(ir.get("y_axis"), field="xychart y_axis")
    x_label = x_axis.get("label")
    y_axis = ir["y_axis"]
    y_label = y_axis.get("label")
    lines = ["xychart-beta", *_accessibility(ir, experimental=experimental), *_title_line(ir)]
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
        values = _xy_series_values(series, index=index, x_min=x_min, x_max=x_max)
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
        *_accessibility(ir, experimental=experimental),
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
